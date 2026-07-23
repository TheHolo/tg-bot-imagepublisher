import asyncio
import logging
import shutil
from datetime import timedelta
from pathlib import Path

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Channel, Job, MediaRecord, Publication
from app.domain.enums import JobStatus
from app.domain.exceptions import ApplicationError, DuplicatePublicationError
from app.services.caption_service import CaptionService
from app.services.download_service import DownloadService
from app.services.job_service import JobService
from app.services.media_service import MediaService
from app.services.publisher_service import TelegramPublisher
from app.services.preview_service import deserialize_post
from app.services.translation_service import TranslationService
from app.utils.tags import merge_tags

logger = logging.getLogger(__name__)


class WorkerPool:
    def __init__(
        self, *, bot: Bot, sessions: async_sessionmaker[AsyncSession], jobs: JobService,
        downloader: DownloadService, media: MediaService, captions: CaptionService,
        publisher: TelegramPublisher, count: int, wakeup: asyncio.Event,
        delete_after_publish: bool, storage: Path, auto_add_source_tags: bool,
        max_tags: int, max_tag_length: int, translator: TranslationService,
    ) -> None:
        self.bot, self.sessions, self.jobs = bot, sessions, jobs
        self.downloader, self.media, self.captions, self.publisher = downloader, media, captions, publisher
        self.translator = translator
        self.count, self.wakeup = count, wakeup
        self.delete_after_publish, self.storage = delete_after_publish, storage
        self.auto_add_source_tags = auto_add_source_tags
        self.max_tags, self.max_tag_length = max_tags, max_tag_length
        self.tasks: list[asyncio.Task] = []
        self.stopping = False

    async def start(self) -> None:
        await self.jobs.recover()
        self.tasks = [asyncio.create_task(self._run(index), name=f"publisher-worker-{index}") for index in range(self.count)]
        self.wakeup.set()

    async def stop(self) -> None:
        self.stopping = True
        self.wakeup.set()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _run(self, index: int) -> None:
        while not self.stopping:
            try:
                job = await self.jobs.claim_next()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker_claim_failed worker=%s", index)
                await asyncio.sleep(2)
                continue
            if not job:
                self.wakeup.clear()
                try:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=10)
                except TimeoutError:
                    pass
                continue
            try:
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                retryable = isinstance(error, ApplicationError) and error.retryable
                await self.jobs.fail(job.id, error, retryable)
                logger.exception("job_failed job_id=%s provider=%s attempt=%s", job.id, job.provider, job.attempts)
                await self._notify(job, f"Задание #{job.id}: ошибка — {error}")
                if retryable and job.attempts < job.max_attempts:
                    self.wakeup.set()

    async def _process(self, job: Job) -> None:
        post = deserialize_post(job.post_data)
        await self.translator.enrich_title(post)
        duplicate = await self.jobs.duplicate(job)
        if duplicate and not job.allow_duplicate:
            raise DuplicatePublicationError(f"Эта публикация уже отправлена в канал {job.channel.alias}")
        downloaded = []
        for item in post.media_items:
            if await self.jobs.is_cancelled(job.id):
                await self.jobs.transition(job.id, JobStatus.CANCELLED)
                return
            downloaded.append(await self.downloader.download(job.id, item))
        await self.jobs.transition(job.id, JobStatus.PROCESSING)
        prepared = [await self.media.prepare(item, job.channel.publish_mode) for item in downloaded]
        if await self.jobs.is_cancelled(job.id):
            await self.jobs.transition(job.id, JobStatus.CANCELLED)
            return
        caption_tags = job.user_tags
        if self.auto_add_source_tags:
            caption_tags = merge_tags(job.user_tags, job.source_tags, self.max_tags, self.max_tag_length)
        caption = self.captions.build(post, caption_tags, job.channel.caption_template or self.captions_template)
        await self.jobs.transition(job.id, JobStatus.PUBLISHING)
        result = await self.publisher.publish(job, post, prepared, job.channel, caption)
        async with self.sessions() as session, session.begin():
            session.add(Publication(
                job_id=job.id, channel_id=job.target_channel_id, telegram_chat_id=result.chat_id,
                telegram_message_ids=result.message_ids, published_at=result.published_at, caption=caption,
            ))
            channel = await session.get(Channel, job.target_channel_id)
            if channel and channel.publish_interval_seconds:
                channel.next_publish_at = result.published_at + timedelta(seconds=channel.publish_interval_seconds)
            elif channel:
                channel.next_publish_at = None
            for source, downloaded_item, prepared_item, message_id in zip(
                post.media_items, downloaded, prepared, result.message_ids, strict=False
            ):
                session.add(MediaRecord(
                    job_id=job.id, source_url=source.url, local_path=str(downloaded_item.path),
                    prepared_path=str(prepared_item.path), filename=source.filename, mime_type=downloaded_item.mime_type,
                    media_type=source.media_type, size=downloaded_item.size, width=downloaded_item.width,
                    height=downloaded_item.height, sort_order=source.order, download_status="completed",
                    publish_status="completed", telegram_message_id=message_id,
                ))
        await self.jobs.transition(job.id, JobStatus.COMPLETED)
        logger.info("job_published job_id=%s provider=%s channel=%s messages=%s", job.id, job.provider, job.channel.alias, len(result.message_ids))
        await self._notify(job, f"Задание #{job.id} успешно опубликовано в {job.channel.alias}.")
        if self.delete_after_publish:
            shutil.rmtree(self.storage / "jobs" / str(job.id), ignore_errors=True)

    @property
    def captions_template(self) -> str:
        from app.services.caption_service import DEFAULT_TEMPLATE
        return DEFAULT_TEMPLATE

    async def _notify(self, job: Job, text: str) -> None:
        try:
            async with self.sessions() as session:
                from app.db.models import User
                user = await session.get(User, job.created_by_user_id)
                if user:
                    await self.bot.send_message(user.telegram_user_id, text)
        except Exception:
            logger.warning("notification_failed job_id=%s", job.id, exc_info=True)
