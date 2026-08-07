from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import InputMediaDocument, InputMediaPhoto

from app.domain.enums import ContentKind, MediaType
from app.domain.exceptions import PublishError, UncertainPublishError
from app.domain.models import PreparedMedia, SourcePost
from app.services.publisher_service import TelegramPublisher


def prepared(index: int, *, as_document: bool = False) -> PreparedMedia:
    return PreparedMedia(Path(f"image-{index}.png"), as_document=as_document, order=index)


def bot_mock() -> SimpleNamespace:
    return SimpleNamespace(
        send_animation=AsyncMock(),
        send_document=AsyncMock(),
        send_message=AsyncMock(),
        send_photo=AsyncMock(),
        send_media_group=AsyncMock(),
        send_video=AsyncMock(),
    )


def news_post() -> SourcePost:
    return SourcePost(
        provider="news-manual",
        source_id="manual-1",
        source_url="",
        normalized_url="manual:1",
        title="Новость",
        body="Текст",
        author_name="Ручной ввод",
        author_url="",
        media_items=[],
        content_kind=ContentKind.NEWS,
    )


async def test_mixed_album_is_sent_as_homogeneous_document_group():
    bot = bot_mock()
    bot.send_media_group.return_value = [SimpleNamespace(message_id=1), SimpleNamespace(message_id=2)]
    publisher = TelegramPublisher(bot)

    result = await publisher.preview(
        -1001,
        [prepared(0), prepared(1, as_document=True)],
        "caption",
    )

    group = bot.send_media_group.await_args.args[1]
    assert all(isinstance(item, InputMediaDocument) for item in group)
    assert not any(isinstance(item, InputMediaPhoto) for item in group)
    assert group[0].caption == "caption"
    assert group[1].caption is None
    assert result.message_ids == [1, 2]


async def test_album_tail_with_one_item_is_sent_without_invalid_single_item_group():
    bot = bot_mock()
    bot.send_media_group.return_value = [SimpleNamespace(message_id=index) for index in range(1, 11)]
    bot.send_photo.return_value = SimpleNamespace(message_id=11)
    publisher = TelegramPublisher(bot)

    result = await publisher.preview(-1001, [prepared(index) for index in range(11)], "caption")

    bot.send_media_group.assert_awaited_once()
    assert len(bot.send_media_group.await_args.args[1]) == 10
    bot.send_photo.assert_awaited_once()
    assert bot.send_photo.await_args.kwargs["caption"] is None
    assert result.message_ids == list(range(1, 12))


async def test_network_error_after_publish_request_is_marked_uncertain_and_not_retryable():
    bot = bot_mock()
    bot.send_photo.side_effect = TelegramNetworkError(method=object(), message="connection lost")
    publisher = TelegramPublisher(bot)

    with pytest.raises(UncertainPublishError) as captured:
        await publisher.preview(-1001, [prepared(0)], "caption")

    assert captured.value.retryable is False
    assert captured.value.code == "uncertain_publish"


async def test_empty_publication_is_rejected_before_calling_telegram():
    bot = bot_mock()

    with pytest.raises(PublishError, match="не содержит"):
        await TelegramPublisher(bot).preview(-1001, [], "caption")

    bot.send_media_group.assert_not_awaited()
    bot.send_photo.assert_not_awaited()
    bot.send_document.assert_not_awaited()


async def test_long_news_failure_after_media_is_marked_as_partial_publish():
    bot = bot_mock()
    bot.send_photo.return_value = SimpleNamespace(message_id=41)
    bot.send_message.side_effect = TelegramBadRequest(
        method=object(), message="message text is too long",
    )

    with pytest.raises(UncertainPublishError, match="частично"):
        await TelegramPublisher(bot).preview(
            -1001, [prepared(0)], "x" * 1025, news_post(),
        )


async def test_split_media_failure_after_first_item_is_marked_as_partial_publish():
    bot = bot_mock()
    bot.send_photo.return_value = SimpleNamespace(message_id=51)
    bot.send_document.side_effect = TelegramBadRequest(
        method=object(), message="document rejected",
    )
    items = [
        PreparedMedia(
            path=None, as_document=False, order=0,
            media_type=MediaType.IMAGE, telegram_file_id="photo-id",
        ),
        PreparedMedia(
            path=None, as_document=True, order=1,
            media_type=MediaType.DOCUMENT, telegram_file_id="document-id",
        ),
    ]

    with pytest.raises(UncertainPublishError, match="частично"):
        await TelegramPublisher(bot).preview(-1001, items, "caption")


async def test_bad_request_before_any_message_remains_regular_publish_error():
    bot = bot_mock()
    bot.send_photo.side_effect = TelegramBadRequest(
        method=object(), message="photo rejected",
    )

    with pytest.raises(PublishError) as captured:
        await TelegramPublisher(bot).preview(-1001, [prepared(0)], "caption")

    assert not isinstance(captured.value, UncertainPublishError)
