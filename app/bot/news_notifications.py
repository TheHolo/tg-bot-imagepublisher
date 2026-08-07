import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from app.bot.news_ui import news_preview_keyboard, news_task_keyboard
from app.db.models import NewsTask
from app.domain.enums import NewsTaskStatus
from app.services.job_service import JobService
from app.services.news_task_service import CompletedNewsTask
from app.services.preview_service import PreviewService, deserialize_post

logger = logging.getLogger(__name__)


class NewsTaskNotifier:
    def __init__(self, *, bot: Bot, jobs: JobService, previews: PreviewService) -> None:
        self.bot = bot
        self.jobs = jobs
        self.previews = previews

    async def progress(self, task: NewsTask) -> None:
        await self._edit_task_message(
            task,
            f"📰 Обработка новости #{task.id}\n"
            f"Текущий этап: {escape(task.stage_message or task.stage)}",
            keep_cancel=True,
        )

    async def failure(self, task: NewsTask) -> None:
        retrying = task.status == NewsTaskStatus.QUEUED
        text = (
            f"📰 Обработка новости #{task.id}\n"
            f"{escape(task.stage_message or 'Ошибка обработки')}"
        )
        if task.error_message:
            text += f"\nОшибка: {escape(task.error_message[:500])}"
        await self._edit_task_message(task, text, keep_cancel=retrying)

    async def complete(self, completed: CompletedNewsTask) -> None:
        if completed.status_message_id is not None:
            try:
                await self.bot.edit_message_text(
                    f"✅ Новость #{completed.task_id} обработана. Черновик #{completed.job_id} готов.",
                    chat_id=completed.origin_chat_id,
                    message_id=completed.status_message_id,
                )
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error).lower():
                    logger.warning(
                        "news_status_complete_edit_failed task_id=%s",
                        completed.task_id,
                        exc_info=True,
                    )
            except TelegramAPIError:
                logger.warning(
                    "news_status_complete_edit_failed task_id=%s",
                    completed.task_id,
                    exc_info=True,
                )
        job = await self.jobs.get(completed.job_id)
        if job is None:
            return
        preview_sent = True
        try:
            await self.previews.send(job, completed.origin_chat_id)
        except Exception as error:
            preview_sent = False
            logger.warning("news_preview_send_failed job_id=%s", job.id, exc_info=True)
            try:
                await self.bot.send_message(
                    completed.origin_chat_id,
                    f"Черновик #{job.id} создан, но показать предпросмотр не удалось: {error}",
                )
            except TelegramAPIError:
                logger.warning("news_preview_error_notice_failed job_id=%s", job.id, exc_info=True)
        post = deserialize_post(job.post_data)
        warnings = post.metadata.get("warnings") or []
        warning_text = (
            "\nПредупреждения модели: " + escape("; ".join(str(item) for item in warnings[:3]))
            if warnings else ""
        )
        await self.bot.send_message(
            completed.origin_chat_id,
            f"📰 <b>Черновик новости #{job.id}</b>\n"
            f"Канал: <code>{escape(job.channel.alias)}</code>\n"
            f"Медиа: {len(post.media_items)}\n"
            + (
                "Предпросмотр отправлен отдельным сообщением."
                if preview_sent else "Предпросмотр не отправлен; черновик можно отредактировать."
            )
            + warning_text,
            parse_mode="HTML",
            reply_markup=news_preview_keyboard(job.id, len(post.media_items)),
        )

    async def _edit_task_message(
        self, task: NewsTask, text: str, *, keep_cancel: bool,
    ) -> None:
        if task.status_message_id is None:
            return
        try:
            await self.bot.edit_message_text(
                text,
                chat_id=task.origin_chat_id,
                message_id=task.status_message_id,
                parse_mode="HTML",
                reply_markup=news_task_keyboard(task.id) if keep_cancel else None,
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                logger.warning("news_status_edit_failed task_id=%s", task.id, exc_info=True)
        except TelegramAPIError:
            logger.warning("news_status_edit_failed task_id=%s", task.id, exc_info=True)
