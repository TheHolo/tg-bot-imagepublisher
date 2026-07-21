from app.bot.router import cancel_tag_input_keyboard, queued_preview_keyboard


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
