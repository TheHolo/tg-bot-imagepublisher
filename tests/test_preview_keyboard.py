from datetime import UTC, datetime, timedelta

from app.bot.router import (
    cancel_tag_input_keyboard,
    caption_input_keyboard,
    channel_selection_keyboard,
    queue_summary_line,
    queued_preview_keyboard,
    replace_channel_line,
    wizard_channel_keyboard,
)
from app.db.models import Channel


def test_queued_preview_keyboard_has_expected_layout():
    keyboard = queued_preview_keyboard(42).inline_keyboard

    assert [len(row) for row in keyboard] == [1, 2, 1, 1]
    assert [button.text for row in keyboard for button in row] == [
        "Опубликовать сейчас",
        "Заменить теги",
        "Добавить теги",
        "Изменить подпись",
        "Отменить публикацию",
    ]
    assert [button.callback_data for row in keyboard for button in row] == [
        "preview_publish:42",
        "preview_tags_replace:42",
        "preview_tags_add:42",
        "preview_caption:42",
        "preview_cancel:42",
    ]


def test_tag_input_keyboard_allows_cancelling_input():
    keyboard = cancel_tag_input_keyboard(42).inline_keyboard

    assert len(keyboard) == 1
    assert keyboard[0][0].text == "Отмена ввода"
    assert keyboard[0][0].callback_data == "tags_input_cancel:42"


def test_caption_input_keyboard_can_restore_automatic_caption_or_cancel():
    keyboard = caption_input_keyboard(42).inline_keyboard

    assert [button.text for row in keyboard for button in row] == [
        "Использовать автоподпись",
        "Отмена ввода",
    ]
    assert [button.callback_data for row in keyboard for button in row] == [
        "caption_auto:42",
        "caption_input_cancel:42",
    ]


def test_channel_selection_keyboard_lists_channels_and_marks_current():
    first = Channel(id=7, alias="arknights", telegram_chat_id="-1001", title="Arknights")
    second = Channel(id=8, alias="endfield", telegram_chat_id="-1002", title="Endfield")

    keyboard = channel_selection_keyboard(42, [first, second], current_channel_id=8).inline_keyboard

    assert [button.text for row in keyboard for button in row] == [
        "arknights — Arknights",
        "✓ endfield — Endfield",
        "Отмена",
    ]
    assert [button.callback_data for row in keyboard for button in row] == [
        "channel_select:42:7",
        "channel_select:42:8",
        "channel_select_cancel:42",
    ]


def test_channel_selection_keyboard_is_paginated_by_ten():
    channels = [
        Channel(
            id=index,
            alias=f"channel-{index:02d}",
            telegram_chat_id=f"-{1000 + index}",
            title=f"Channel {index:02d}",
        )
        for index in range(1, 24)
    ]

    first = channel_selection_keyboard(42, channels, current_channel_id=15, page=0)
    middle = channel_selection_keyboard(42, channels, current_channel_id=15, page=1)
    last = channel_selection_keyboard(42, channels, current_channel_id=15, page=2)

    first_buttons = [button for row in first.inline_keyboard for button in row]
    middle_buttons = [button for row in middle.inline_keyboard for button in row]
    last_buttons = [button for row in last.inline_keyboard for button in row]
    assert [
        button.callback_data for button in first_buttons
        if button.callback_data.startswith("channel_select:42:")
    ] == [f"channel_select:42:{index}" for index in range(1, 11)]
    assert [
        button.callback_data for button in middle_buttons
        if button.callback_data.startswith("channel_select:42:")
    ] == [f"channel_select:42:{index}" for index in range(11, 21)]
    assert next(
        button.text for button in middle_buttons
        if button.callback_data == "channel_select:42:15"
    ).startswith("✓ ")
    assert "channel_select_page:42:0" in [button.callback_data for button in middle_buttons]
    assert "channel_select_page:42:2" in [button.callback_data for button in middle_buttons]
    assert [
        button.callback_data for button in last_buttons
        if button.callback_data.startswith("channel_select:42:")
    ] == ["channel_select:42:21", "channel_select:42:22", "channel_select:42:23"]
    assert "channel_select_page:42:1" in [button.callback_data for button in last_buttons]
    assert "channel_select_page:42:3" not in [button.callback_data for button in last_buttons]


def test_wizard_channel_keyboard_uses_buttons_without_job_id():
    channels = [
        Channel(
            id=7, alias="artwork", telegram_chat_id="-1001", title="Artwork",
            is_enabled=True,
        ),
        Channel(
            id=8, alias="archive", telegram_chat_id="-1002", title="Archive",
            is_enabled=False,
        ),
    ]

    buttons = [
        button
        for row in wizard_channel_keyboard(channels).inline_keyboard
        for button in row
    ]

    assert [button.text for button in buttons] == [
        "artwork — Artwork",
        "Отменить создание",
    ]
    assert [button.callback_data for button in buttons] == [
        "wizard_channel:0:7",
        "wizard_cancel",
    ]


def test_replace_channel_line_keeps_the_rest_of_preview():
    text = "Источник: Pixiv\nКанал: artwork\n\nТеги: #art"

    assert replace_channel_line(text, "arknights") == "Источник: Pixiv\nКанал: arknights\n\nТеги: #art"


def test_channel_queue_summary_shows_count_and_total_time():
    now = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)

    assert queue_summary_line(7, now + timedelta(hours=13, minutes=53, seconds=10), now) == (
        "Всего постов: 7 · Вся очередь: через 13ч 53м 10с"
    )
