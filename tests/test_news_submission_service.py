from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.news.models import NewsSourceKind, NewsSourceRequest
from app.services.news_submission_service import NewsSubmissionService


def make_service(*, jobs=None, tasks=None, enabled: bool = True):
    jobs = jobs or SimpleNamespace(
        ensure_user=AsyncMock(return_value=SimpleNamespace(id=5)),
        get_channel=AsyncMock(),
        get_preferred_channel=AsyncMock(
            return_value=SimpleNamespace(id=9, alias="news"),
        ),
    )
    tasks = tasks or SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(id=11)),
    )
    return NewsSubmissionService(
        jobs=jobs,
        tasks=tasks,
        default_channel_alias="news",
        model_name="gemma4:12b",
        max_attempts=3,
        enabled=enabled,
    ), jobs, tasks


async def test_news_submission_uses_preferred_channel_and_source_text():
    service, jobs, tasks = make_service()

    queued = await service.create(
        telegram_user_id=42,
        username="editor",
        display_name="Editor",
        origin_chat_id=42,
        request=NewsSourceRequest(NewsSourceKind.MANUAL, "Исходный текст"),
        user_tags=["новости"],
    )

    assert queued.channel.alias == "news"
    jobs.get_preferred_channel.assert_awaited_once_with(5, "news")
    tasks.create.assert_awaited_once_with(
        user_id=5,
        channel_id=9,
        origin_chat_id=42,
        source_kind="manual",
        input_payload={"kind": "manual", "source_text": "Исходный текст"},
        user_tags=["новости"],
        model_name="gemma4:12b",
        max_attempts=3,
    )


async def test_forwarded_telegram_submission_keeps_only_structured_payload():
    channel = SimpleNamespace(id=10, alias="archive")
    jobs = SimpleNamespace(
        ensure_user=AsyncMock(return_value=SimpleNamespace(id=6)),
        get_channel=AsyncMock(return_value=channel),
        get_preferred_channel=AsyncMock(),
    )
    service, _, tasks = make_service(jobs=jobs)
    telegram = {"text": "Пересланный текст", "message_id": 77}

    await service.create(
        telegram_user_id=42,
        username=None,
        display_name="Editor",
        origin_chat_id=-1001,
        request=NewsSourceRequest(NewsSourceKind.TELEGRAM, "ignored fallback"),
        extra_payload={"telegram": telegram},
        channel_alias="archive",
    )

    jobs.get_channel.assert_awaited_once_with("archive")
    payload = tasks.create.await_args.kwargs["input_payload"]
    assert payload == {"kind": "telegram", "telegram": telegram}


async def test_news_submission_rejects_disabled_pipeline_and_missing_channel():
    disabled, jobs, tasks = make_service(enabled=False)
    request = NewsSourceRequest(NewsSourceKind.WEBSITE, "https://example.com/news")

    with pytest.raises(ValueError, match="NEWS_WORKER_TOKEN"):
        await disabled.create(
            telegram_user_id=42,
            username=None,
            display_name="Editor",
            origin_chat_id=42,
            request=request,
        )
    jobs.ensure_user.assert_not_awaited()
    tasks.create.assert_not_awaited()

    enabled, jobs, tasks = make_service()
    jobs.get_preferred_channel.return_value = None
    with pytest.raises(ValueError, match="Нет активного канала"):
        await enabled.create(
            telegram_user_id=42,
            username=None,
            display_name="Editor",
            origin_chat_id=42,
            request=request,
        )
    tasks.create.assert_not_awaited()
