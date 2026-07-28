import asyncio
from dataclasses import dataclass

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from app.bot.menu import configure_bot_ui
from app.bot.middleware import AdminOnlyMiddleware
from app.bot.router import build_router
from app.config import Settings
from app.db.models import Channel
from app.db.session import create_database, create_schema
from app.providers.deviantart import DeviantArtProvider
from app.providers.direct_image import DirectImageProvider
from app.providers.pixiv import PixivProvider
from app.providers.registry import ProviderRegistry
from app.queue.worker import WorkerPool
from app.services.caption_service import CaptionService
from app.services.download_service import DownloadService
from app.services.health_service import HealthService
from app.services.ingest_service import IngestService
from app.services.job_service import JobService
from app.services.media_service import MediaService
from app.services.preview_service import PreviewService
from app.services.publisher_service import TelegramPublisher
from app.services.translation_service import TranslationService


@dataclass
class Application:
    settings: Settings
    bot: Bot
    dispatcher: Dispatcher
    http: aiohttp.ClientSession
    engine: object
    workers: WorkerPool

    async def run(self) -> None:
        try:
            await configure_bot_ui(self.bot)
            await self.workers.start()
            await self.dispatcher.start_polling(self.bot, allowed_updates=self.dispatcher.resolve_used_update_types())
        finally:
            await self.workers.stop()
            await self.http.close()
            await self.bot.session.close()
            await self.engine.dispose()


async def bootstrap(settings: Settings | None = None) -> Application:
    settings = settings or Settings()
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    engine, sessions = create_database(settings.database_url)
    await create_schema(engine)
    await _sync_channels(sessions, settings)
    timeout = aiohttp.ClientTimeout(total=settings.download_timeout)
    connector = None
    proxy_url = settings.proxy_url
    default_proxy = proxy_url
    if proxy_url and proxy_url.lower().startswith(("socks4://", "socks5://")):
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError as error:
            raise RuntimeError("Для SOCKS-прокси установите зависимости: pip install -e '.[proxy]'") from error
        connector = ProxyConnector.from_url(proxy_url)
        default_proxy = None
    http = aiohttp.ClientSession(
        timeout=timeout, headers={"User-Agent": "TelegramImagePublisher/0.1"},
        connector=connector, proxy=default_proxy,
    )
    bot_session = AiohttpSession(proxy=proxy_url) if proxy_url else None
    bot = Bot(settings.bot_token, session=bot_session)
    registry = ProviderRegistry([
        PixivProvider(
            http, settings.pixiv_cookies,
            media_limit_enabled=settings.pixiv_media_limit_enabled,
            max_images=settings.pixiv_max_images,
        ),
        DeviantArtProvider(http),
        DirectImageProvider(http, settings.max_download_bytes),
    ])
    jobs = JobService(sessions)
    ingest = IngestService(registry, settings.max_tags, settings.max_tag_length, settings.max_urls_per_message)
    downloader = DownloadService(http, settings.storage_path, settings.max_download_bytes)
    media = MediaService()
    captions = CaptionService()
    publisher = TelegramPublisher(bot)
    translator = TranslationService(
        http, enabled=settings.auto_translate_titles, timeout=settings.translation_timeout
    )
    previews = PreviewService(
        downloader=downloader, media=media, captions=captions, publisher=publisher,
        storage=settings.storage_path, auto_add_source_tags=settings.auto_add_source_tags,
        max_tags=settings.max_tags, max_tag_length=settings.max_tag_length, translator=translator,
    )
    wakeup = asyncio.Event()
    workers = WorkerPool(
        bot=bot, sessions=sessions, jobs=jobs,
        downloader=downloader, media=media, captions=captions, publisher=publisher,
        count=settings.worker_count, wakeup=wakeup,
        delete_after_publish=settings.delete_files_after_publish, storage=settings.storage_path,
        auto_add_source_tags=settings.auto_add_source_tags,
        max_tags=settings.max_tags, max_tag_length=settings.max_tag_length, translator=translator,
    )
    health = HealthService(
        bot=bot, sessions=sessions, workers=workers, storage=settings.storage_path,
        registry=registry, database_url=settings.database_url,
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    middleware = AdminOnlyMiddleware(settings.admin_ids)
    router = build_router(ingest, jobs, previews, translator, health, wakeup, registry, settings)
    router.message.outer_middleware(middleware)
    router.callback_query.outer_middleware(middleware)
    dispatcher.include_router(router)
    return Application(settings, bot, dispatcher, http, engine, workers)


async def _sync_channels(sessions, settings: Settings) -> None:
    async with sessions() as session, session.begin():
        for alias, values in settings.channels_json.items():
            channel = await session.scalar(select(Channel).where(Channel.alias == alias))
            is_new = channel is None
            if channel is None:
                channel = Channel(
                    alias=alias,
                    telegram_chat_id=str(values["chat_id"]),
                    title=values.get("title", alias),
                    is_default=alias == settings.default_channel_alias,
                )
                session.add(channel)
            channel.telegram_chat_id = str(values["chat_id"])
            channel.title = values.get("title", alias)
            channel.publish_mode = values.get("publish_mode", "auto")
            if is_new and "publish_interval_seconds" in values:
                channel.publish_interval_seconds = int(values["publish_interval_seconds"])
            channel.caption_template = values.get("caption_template")
            channel.is_enabled = values.get("enabled", True)
        await session.flush()
        default_channel = await session.scalar(select(Channel).where(Channel.is_default.is_(True)))
        if default_channel is None:
            configured_default = await session.scalar(
                select(Channel).where(Channel.alias == settings.default_channel_alias)
            )
            if configured_default is not None:
                configured_default.is_default = True
