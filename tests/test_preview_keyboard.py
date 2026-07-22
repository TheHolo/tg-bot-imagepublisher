from app.bot.router import (
    cancel_tag_input_keyboard,
    channel_selection_keyboard,
    queued_preview_keyboard,
    replace_channel_line,
)
from app.db.models import Channel


def test_queued_preview_keyboard_has_expected_three_row_layout():
    keyboard = queued_preview_keyboard(42).inline_keyboard

    assert [len(row) for row in keyboard] == [1, 2, 1]
    assert [button.text for row in keyboard for button in row] == [
        "Опубликовать сейчас",
        "Заменить теги",
        "Добавить теги",
        "Отменить публикацию",
    ]
    assert [button.callback_data for row in keyboard for button in row] == [
        "preview_publish:42",
        "preview_tags_replace:42",
        "preview_tags_add:42",
        "preview_cancel:42",
    ]


def test_tag_input_keyboard_allows_cancelling_input():
    keyboard = cancel_tag_input_keyboard(42).inline_keyboard

    assert len(keyboard) == 1
    assert keyboard[0][0].text == "Отмена ввода"
    assert keyboard[0][0].callback_data == "tags_input_cancel:42"


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


def test_replace_channel_line_keeps_the_rest_of_preview():
    text = "Источник: Pixiv\nКанал: artwork\n\nТеги: #art"

    assert replace_channel_line(text, "arknights") == "Источник: Pixiv\nКанал: arknights\n\nТеги: #art"
