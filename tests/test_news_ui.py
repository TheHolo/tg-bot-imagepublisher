import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, MessageOriginChannel, PhotoSize, User

from app.bot import news_ui
from app.bot.news_ui import build_news_router
from app.bot.states import CreateNews
from app.news.models import NewsSourceKind


class RecordingBot:
    def __init__(self) -> None:
        self.requests = []
        self.next_message_id = 900

    async def __call__(self, method, request_timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage):
            self.next_message_id += 1
            return Message(
                message_id=self.next_message_id,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                from_user=User(id=999, is_bot=True, first_name="Bot"),
                text=method.text,
            ).as_(self)
        return True


def make_forwarded_photo(
    bot: RecordingBot,
    *,
    message_id: int,
    source_message_id: int,
    media_group_id: str = "album-1",
    caption: str | None = None,
) -> Message:
    now = datetime.now(UTC)
    return Message(
        message_id=message_id,
        date=now,
        chat=Chat(id=10, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Admin"),
        forward_origin=MessageOriginChannel(
            date=now,
            chat=Chat(
                id=-100123,
                type="channel",
                title="Source channel",
                username="source_channel",
            ),
            message_id=source_message_id,
        ),
        media_group_id=media_group_id,
        caption=caption,
        photo=[PhotoSize(
            file_id=f"file-{message_id}",
            file_unique_id=f"unique-{message_id}",
            width=1280,
            height=720,
            file_size=1000,
        )],
    ).as_(bot)


def make_state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=10, user_id=42),
    )


def make_router(submissions=None):
    queued = SimpleNamespace(
        task=SimpleNamespace(id=77),
        channel=SimpleNamespace(alias="news"),
    )
    submissions = submissions or SimpleNamespace(
        create=AsyncMock(return_value=queued),
    )
    tasks = SimpleNamespace(
        set_status_message=AsyncMock(),
        cancel=AsyncMock(),
    )
    router = build_news_router(
        submissions=submissions,
        tasks=tasks,
        jobs=SimpleNamespace(),
        previews=SimpleNamespace(),
    )
    return router, submissions, tasks


def handler(router, name: str):
    return next(
        item.callback for item in router.message.handlers
        if item.callback.__name__ == name
    )


async def wait_for_album_flush() -> None:
    await asyncio.sleep(0.05)


@pytest.fixture(autouse=True)
def fast_album_debounce(monkeypatch):
    monkeypatch.setattr(news_ui, "_FORWARDED_ALBUM_DEBOUNCE_SECONDS", 0.01)


async def test_news_wizard_aggregates_forwarded_album_and_clears_state_on_success():
    router, submissions, tasks = make_router()
    receive_source = handler(router, "receive_source")
    bot = RecordingBot()
    state = make_state()
    await state.set_state(CreateNews.waiting_for_source)

    await receive_source(make_forwarded_photo(
        bot, message_id=102, source_message_id=502,
    ), state)
    await receive_source(make_forwarded_photo(
        bot, message_id=101, source_message_id=501, caption="Текст новости",
    ), state)

    submissions.create.assert_not_awaited()
    assert await state.get_state() == CreateNews.waiting_for_source.state
    await wait_for_album_flush()

    submissions.create.assert_awaited_once()
    call = submissions.create.await_args.kwargs
    assert call["request"].kind is NewsSourceKind.TELEGRAM
    assert call["request"].value == "https://t.me/source_channel/501"
    payload = call["extra_payload"]
    assert payload["source_text"] == "Текст новости"
    assert [item["telegram_file_id"] for item in payload["media"]] == [
        "file-101", "file-102",
    ]
    assert payload["telegram"]["media"] == payload["media"]
    assert await state.get_state() is None
    tasks.set_status_message.assert_awaited_once()


async def test_global_forwarded_handler_aggregates_and_deduplicates_album_updates():
    router, submissions, _ = make_router()
    receive_forwarded = handler(router, "receive_forwarded")
    bot = RecordingBot()
    first = make_forwarded_photo(
        bot, message_id=201, source_message_id=601, caption="Глобальная новость",
    )
    second = make_forwarded_photo(bot, message_id=202, source_message_id=602)

    await receive_forwarded(first)
    await receive_forwarded(first)
    await receive_forwarded(second)
    await wait_for_album_flush()

    submissions.create.assert_awaited_once()
    payload = submissions.create.await_args.kwargs["extra_payload"]
    assert payload["source_text"] == "Глобальная новость"
    assert [item["telegram_file_id"] for item in payload["media"]] == [
        "file-201", "file-202",
    ]


async def test_forwarded_album_background_error_is_reported_and_wizard_remains_active(
    caplog,
):
    submissions = SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("database down")))
    router, _, _ = make_router(submissions)
    receive_source = handler(router, "receive_source")
    bot = RecordingBot()
    state = make_state()
    await state.set_state(CreateNews.waiting_for_source)
    caplog.set_level(logging.ERROR, logger="app.bot.news_ui")

    await receive_source(make_forwarded_photo(
        bot,
        message_id=301,
        source_message_id=701,
        caption="Новость с ошибкой",
    ), state)
    await wait_for_album_flush()

    assert await state.get_state() == CreateNews.waiting_for_source.state
    assert news_ui._PENDING_FORWARDED_ALBUM_STATE_KEY not in await state.get_data()
    answers = [request.text for request in bot.requests if isinstance(request, SendMessage)]
    assert answers == ["Не удалось обработать пересланный альбом. Повторите отправку."]
    assert "news_forwarded_album_flush_failed" in caplog.text


async def test_cancelled_news_wizard_discards_pending_forwarded_album():
    router, submissions, _ = make_router()
    receive_source = handler(router, "receive_source")
    bot = RecordingBot()
    state = make_state()
    await state.set_state(CreateNews.waiting_for_source)

    await receive_source(make_forwarded_photo(
        bot,
        message_id=401,
        source_message_id=801,
        caption="Уже отменённая новость",
    ), state)
    await state.clear()
    await wait_for_album_flush()

    submissions.create.assert_not_awaited()


async def test_captionless_forwarded_album_keeps_wizard_ready_for_another_source():
    router, submissions, _ = make_router()
    receive_source = handler(router, "receive_source")
    bot = RecordingBot()
    state = make_state()
    await state.set_state(CreateNews.waiting_for_source)

    await receive_source(make_forwarded_photo(
        bot, message_id=501, source_message_id=901,
    ), state)
    await receive_source(make_forwarded_photo(
        bot, message_id=502, source_message_id=902,
    ), state)
    await wait_for_album_flush()

    submissions.create.assert_not_awaited()
    assert await state.get_state() == CreateNews.waiting_for_source.state
    assert news_ui._PENDING_FORWARDED_ALBUM_STATE_KEY not in await state.get_data()
    answers = [request.text for request in bot.requests if isinstance(request, SendMessage)]
    assert answers == ["В пересланном альбоме нет текста для создания новости."]
