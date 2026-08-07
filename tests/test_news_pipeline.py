import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db.models import Channel, User
from app.db.models import NewsTask as StoredNewsTask
from app.db.session import create_database, create_schema
from app.domain.enums import ContentKind, JobStatus, MediaType, NewsTaskStatus
from app.domain.exceptions import PublishError
from app.domain.models import MediaItem, PreparedMedia, SourcePost
from app.news.models import (
    ExtractedNewsSource,
    NewsMedia,
    NewsMediaKind,
    NewsSourceKind,
)
from app.news.worker_models import NewsDraft, WorkerResult
from app.services.job_service import JobService
from app.services.news_render_service import NewsRenderService
from app.services.news_task_service import NewsTaskLeaseError, NewsTaskService
from app.services.preview_service import PreviewService
from app.services.publisher_service import TelegramPublisher


def news_post(*, media: list[MediaItem] | None = None) -> SourcePost:
    return SourcePost(
        provider="news-website",
        source_id="story-1",
        source_url="https://example.com/story",
        normalized_url="https://example.com/story",
        title="Заголовок <тест>",
        description="Лид & детали",
        body="Основной текст",
        author_name="Example & Co",
        author_url="https://example.com",
        media_items=media or [],
        content_kind=ContentKind.NEWS,
    )


def test_news_renderer_escapes_model_text_and_keeps_attribution_for_manual_edits():
    renderer = NewsRenderService()
    post = news_post()

    generated = renderer.build(post, ["важное"])
    edited = renderer.build_custom("Исправлено <b>вручную</b>", post, ["важное"])

    assert "<b>Заголовок &lt;тест&gt;</b>" in generated
    assert "Лид &amp; детали" in generated
    assert '<a href="https://example.com/story">Example &amp; Co</a>' in generated
    assert "#важное" in generated
    assert "Исправлено &lt;b&gt;вручную&lt;/b&gt;" in edited
    assert "Источник:" in edited
    assert "#важное" in edited


def test_news_renderer_rejects_telegram_overflow():
    with pytest.raises(PublishError, match="4096"):
        NewsRenderService().build_custom("x" * 4097)


def test_news_renderer_counts_link_label_instead_of_hidden_href():
    post = news_post()
    post.source_url = "https://example.com/" + "x" * 5000

    rendered = NewsRenderService().build(post, [])

    assert rendered.endswith("</a>")
    assert post.source_url in rendered


async def test_text_only_news_uses_send_message_but_empty_artwork_remains_invalid():
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=8)))
    publisher = TelegramPublisher(bot)

    result = await publisher.preview(-1001, [], "Новость", news_post())

    assert result.message_ids == [8]
    assert result.media_count == 0
    bot.send_message.assert_awaited_once_with(-1001, "Новость", parse_mode="HTML")
    with pytest.raises(PublishError, match="не содержит"):
        await publisher.preview(-1001, [], "artwork")


async def test_text_only_news_preview_skips_downloader_media_and_artwork_translation(tmp_path):
    publisher = SimpleNamespace(preview=AsyncMock())
    downloader = SimpleNamespace(download=AsyncMock())
    media = SimpleNamespace(prepare=AsyncMock())
    translator = SimpleNamespace(enrich_title=AsyncMock())
    service = PreviewService(
        downloader=downloader,
        media=media,
        captions=SimpleNamespace(),
        publisher=publisher,
        storage=tmp_path,
        auto_add_source_tags=True,
        max_tags=20,
        max_tag_length=64,
        translator=translator,
    )
    post = news_post()
    job = SimpleNamespace(
        id=3,
        post_data={
            "content_kind": "news",
            "provider": post.provider,
            "source_id": post.source_id,
            "source_url": post.source_url,
            "normalized_url": post.normalized_url,
            "title": post.title,
            "description": post.description,
            "body": post.body,
            "author_name": post.author_name,
            "author_url": post.author_url,
            "source_tags": [],
            "media_items": [],
            "metadata": {},
        },
        user_tags=[],
        source_tags=[],
        caption_override=None,
        channel=SimpleNamespace(caption_template=None, publish_mode="auto"),
    )

    await service.send(job, 42)

    downloader.download.assert_not_awaited()
    media.prepare.assert_not_awaited()
    translator.enrich_title.assert_not_awaited()
    publisher.preview.assert_awaited_once()
    assert publisher.preview.await_args.args[1] == []


async def _news_context(tmp_path, filename: str):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / filename}")
    await create_schema(engine)
    async with sessions() as session, session.begin():
        user = User(telegram_user_id=100)
        channel = Channel(
            alias="news", telegram_chat_id="-1001", title="News", is_default=True,
        )
        session.add_all([user, channel])
        await session.flush()
        ids = user.id, channel.id
    return engine, sessions, *ids


async def test_news_task_lifecycle_creates_editable_durable_job(tmp_path):
    engine, sessions, user_id, channel_id = await _news_context(tmp_path, "news-task.db")
    tasks = NewsTaskService(sessions, lease_extension_seconds=1800)
    media = NewsMedia(
        kind=NewsMediaKind.VIDEO,
        telegram_file_id="video-file-id",
        filename="clip.mp4",
        metadata={"telegram_file_unique_id": "video-unique"},
    )
    created = await tasks.create(
        user_id=user_id,
        channel_id=channel_id,
        origin_chat_id=100,
        source_kind="manual",
        input_payload={
            "kind": "manual",
            "source_text": "Исходный текст",
            "media": [media.to_dict()],
        },
        user_tags=["редакция"],
        model_name="gemma4:12b",
        max_attempts=3,
    )

    assert await tasks.lease(
        worker_id="wrong-model",
        lease_seconds=3600,
        model_name="other:1b",
    ) is None
    leased = await tasks.lease(
        worker_id="home",
        lease_seconds=3600,
        source_types=["manual"],
        model_name="gemma4:12b",
    )
    assert leased is not None
    assert leased.id == created.id
    assert leased.model_name == "gemma4:12b"

    before_progress = (await tasks.get(created.id)).lease_expires_at
    task, changed = await tasks.progress(
        created.id,
        lease_token=leased.lease_token,
        stage="llm_processing",
        message="Локальная модель gemma4:12b создаёт черновик",
    )
    assert changed is True
    assert task.lease_expires_at >= before_progress

    result = WorkerResult(
        source=ExtractedNewsSource(
            kind=NewsSourceKind.MANUAL,
            source_id="manual-1",
            source_url=None,
            normalized_url=None,
            title="Исходный текст",
            raw_text="Исходный текст",
        ),
        draft=NewsDraft(
            headline="Готовая новость",
            lead="Короткий лид",
            body="Проверенный основной текст",
            suggested_tags=["локальная_модель"],
            facts_used=["Исходный текст"],
        ),
    )
    completed = await tasks.complete(
        created.id,
        lease_token=leased.lease_token,
        result_payload=result.model_dump(mode="json"),
    )

    jobs = JobService(sessions)
    job = await jobs.get(completed.job_id)
    assert job.status == JobStatus.WAITING_CONFIRMATION
    assert job.content_kind == ContentKind.NEWS
    assert job.post_data["body"] == "Проверенный основной текст"
    assert job.post_data["author_name"] == "Ручной ввод"
    assert job.post_data["media_items"][0]["telegram_file_id"] == "video-file-id"
    assert job.post_data["media_items"][0]["media_type"] == MediaType.VIDEO

    updated = await jobs.add_media(
        job.id,
        MediaItem(
            url="telegram-media:photo",
            filename="photo.jpg",
            order=0,
            telegram_file_id="photo-file-id",
        ),
    )
    assert len(updated.post_data["media_items"]) == 2
    updated = await jobs.remove_media(job.id, 0)
    assert updated.post_data["media_items"][0]["order"] == 0
    assert updated.post_data["media_items"][0]["telegram_file_id"] == "photo-file-id"

    stored_task = await tasks.get(created.id)
    assert stored_task.status == NewsTaskStatus.COMPLETED
    assert stored_task.job_id == job.id
    await engine.dispose()


async def test_mixed_telegram_file_ids_are_not_retyped_as_documents():
    bot = SimpleNamespace(
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=1)),
        send_document=AsyncMock(return_value=SimpleNamespace(message_id=2)),
        send_media_group=AsyncMock(),
    )
    publisher = TelegramPublisher(bot)
    items = [
        PreparedMedia(
            path=None,
            as_document=False,
            order=0,
            media_type=MediaType.IMAGE,
            telegram_file_id="photo-id",
        ),
        PreparedMedia(
            path=None,
            as_document=True,
            order=1,
            media_type=MediaType.DOCUMENT,
            telegram_file_id="document-id",
        ),
    ]

    result = await publisher.preview(-1001, items, "caption")

    bot.send_media_group.assert_not_awaited()
    bot.send_photo.assert_awaited_once()
    bot.send_document.assert_awaited_once()
    assert result.message_ids == [1, 2]


async def test_news_task_cancel_invalidates_worker_lease(tmp_path):
    engine, sessions, user_id, channel_id = await _news_context(tmp_path, "cancel.db")
    tasks = NewsTaskService(sessions)
    created = await tasks.create(
        user_id=user_id,
        channel_id=channel_id,
        origin_chat_id=100,
        source_kind="manual",
        input_payload={"kind": "manual", "source_text": "Текст"},
        user_tags=[],
        model_name="gemma4:12b",
        max_attempts=1,
    )
    leased = await tasks.lease(worker_id="home", lease_seconds=3600)
    assert leased is not None

    assert await tasks.cancel(created.id) is True
    with pytest.raises(NewsTaskLeaseError):
        await tasks.progress(
            created.id,
            lease_token=leased.lease_token,
            stage="validating",
            message="Проверяем",
        )
    assert await tasks.cancel(created.id) is False
    assert (await tasks.get(created.id)).status == NewsTaskStatus.CANCELLED
    await engine.dispose()


async def test_concurrent_workers_cannot_lease_same_news_task(tmp_path):
    engine, sessions, user_id, channel_id = await _news_context(tmp_path, "lease-race.db")
    tasks = NewsTaskService(sessions)
    await tasks.create(
        user_id=user_id,
        channel_id=channel_id,
        origin_chat_id=100,
        source_kind="manual",
        input_payload={"kind": "manual", "source_text": "Текст"},
        user_tags=[],
        model_name="gemma4:12b",
        max_attempts=2,
    )

    results = await asyncio.gather(
        tasks.lease(worker_id="home-a", lease_seconds=3600),
        tasks.lease(worker_id="home-b", lease_seconds=3600),
    )

    assert sum(item is not None for item in results) == 1
    await engine.dispose()


async def test_expired_last_attempt_becomes_failed_and_cannot_be_cancelled(tmp_path):
    engine, sessions, user_id, channel_id = await _news_context(tmp_path, "expired.db")
    tasks = NewsTaskService(sessions)
    created = await tasks.create(
        user_id=user_id,
        channel_id=channel_id,
        origin_chat_id=100,
        source_kind="manual",
        input_payload={"kind": "manual", "source_text": "Текст"},
        user_tags=[],
        model_name="gemma4:12b",
        max_attempts=1,
    )
    assert await tasks.lease(worker_id="home", lease_seconds=60) is not None
    async with sessions() as session, session.begin():
        stored = await session.get(StoredNewsTask, created.id)
        stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    expired = await tasks.expire_exhausted()

    assert [item.id for item in expired] == [created.id]
    assert expired[0].status == NewsTaskStatus.FAILED
    assert await tasks.cancel(created.id) is False
    await engine.dispose()
