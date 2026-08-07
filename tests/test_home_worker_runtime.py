import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.news.home_runtime import HomeNewsWorker, _TaskProgress
from app.news.models import ExtractedNewsSource, NewsSourceKind
from app.news.worker_errors import LeaseLostError
from app.news.worker_models import NewsDraft, NewsTask


def make_task() -> NewsTask:
    return NewsTask.model_validate(
        {
            "id": 42,
            "lease_token": "lease",
            "input_payload": {"kind": "manual", "source_text": "Исходный текст"},
        }
    )


def make_source() -> ExtractedNewsSource:
    return ExtractedNewsSource(
        kind=NewsSourceKind.MANUAL,
        source_id="manual-42",
        source_url=None,
        normalized_url=None,
        title="",
        raw_text="Исходный текст",
    )


def make_draft() -> NewsDraft:
    return NewsDraft(headline="Новость", body="Текст новости")


async def test_worker_extracts_rewrites_reports_stages_and_completes():
    api = SimpleNamespace(
        lease=AsyncMock(return_value=make_task()),
        progress=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
    )
    extractor = SimpleNamespace(extract=AsyncMock(return_value=make_source()))
    ollama = SimpleNamespace(model="gemma4:12b", rewrite=AsyncMock(return_value=make_draft()))
    worker = HomeNewsWorker(api=api, extractor=extractor, ollama=ollama)

    assert await worker.run_once() is True

    extractor.extract.assert_awaited_once()
    ollama.rewrite.assert_awaited_once_with(make_source())
    api.complete.assert_awaited_once()
    result = api.complete.await_args.args[1]
    assert result.source.source_id == "manual-42"
    assert result.source.raw_text.startswith("processed-locally sha256=")
    assert "characters=14" in result.source.raw_text
    assert "Исходный текст" not in result.source.raw_text
    assert result.draft.headline == "Новость"
    stages = [call.args[1] for call in api.progress.await_args_list]
    assert stages == ["extracting_content", "llm_processing", "validating"]
    api.fail.assert_not_awaited()


async def test_worker_reports_non_retryable_invalid_source():
    empty = make_source()
    empty.raw_text = ""
    api = SimpleNamespace(
        lease=AsyncMock(return_value=make_task()),
        progress=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
    )
    extractor = SimpleNamespace(extract=AsyncMock(return_value=empty))
    ollama = SimpleNamespace(model="gemma4:12b", rewrite=AsyncMock())
    worker = HomeNewsWorker(api=api, extractor=extractor, ollama=ollama)

    assert await worker.run_once() is True

    api.fail.assert_awaited_once()
    assert api.fail.await_args.kwargs["retryable"] is False
    ollama.rewrite.assert_not_awaited()


async def test_worker_materializes_forwarded_telegram_payload_without_url_extractor():
    task = NewsTask.model_validate(
        {
            "id": 43,
            "lease_token": "lease",
            "input_payload": {
                "kind": "telegram",
                "source_text": "Текст пересланного поста",
                "telegram": {
                    "message_id": 99,
                    "chat_id": "-100123",
                    "chat_title": "Канал",
                    "media": [{"kind": "photo", "file_id": "telegram-photo-id"}],
                },
            },
        }
    )
    api = SimpleNamespace(
        lease=AsyncMock(return_value=task),
        progress=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
    )
    extractor = SimpleNamespace(extract=AsyncMock())
    ollama = SimpleNamespace(model="gemma4:12b", rewrite=AsyncMock(return_value=make_draft()))
    worker = HomeNewsWorker(api=api, extractor=extractor, ollama=ollama)

    assert await worker.run_once() is True

    extractor.extract.assert_not_awaited()
    source = ollama.rewrite.await_args.args[0]
    assert source.kind is NewsSourceKind.TELEGRAM
    assert source.source_id == "-100123:99"
    assert source.media[0].telegram_file_id == "telegram-photo-id"
    api.complete.assert_awaited_once()


async def test_worker_cancels_local_llm_when_heartbeat_loses_lease(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def never_finishes(_source):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def lose_lease(_progress):
        await started.wait()
        raise LeaseLostError("lease cancelled")

    monkeypatch.setattr(_TaskProgress, "keepalive", lose_lease)
    api = SimpleNamespace(
        lease=AsyncMock(return_value=make_task()),
        progress=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
    )
    extractor = SimpleNamespace(extract=AsyncMock(return_value=make_source()))
    ollama = SimpleNamespace(model="gemma4:12b", rewrite=never_finishes)

    assert await HomeNewsWorker(api=api, extractor=extractor, ollama=ollama).run_once()

    assert cancelled.is_set()
    api.complete.assert_not_awaited()
    api.fail.assert_not_awaited()
