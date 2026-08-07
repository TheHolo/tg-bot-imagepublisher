from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.news_notifications import NewsTaskNotifier
from app.domain.enums import NewsTaskStatus
from app.domain.models import SourcePost
from app.services.job_service import serialize_post
from app.services.news_task_service import CompletedNewsTask


def make_notifier(*, job=None):
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(),
    )
    jobs = SimpleNamespace(get=AsyncMock(return_value=job))
    previews = SimpleNamespace(send=AsyncMock())
    return NewsTaskNotifier(bot=bot, jobs=jobs, previews=previews), bot, previews


async def test_progress_notification_escapes_worker_message_and_keeps_cancel_button():
    notifier, bot, _ = make_notifier()
    task = SimpleNamespace(
        id=4,
        origin_chat_id="42",
        status_message_id=8,
        stage="llm_processing",
        stage_message="model <working>",
    )

    await notifier.progress(task)

    call = bot.edit_message_text.await_args
    assert "model &lt;working&gt;" in call.args[0]
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "news_task_cancel:4"


async def test_failure_notification_hides_cancel_button_after_final_failure():
    notifier, bot, _ = make_notifier()
    task = SimpleNamespace(
        id=5,
        origin_chat_id="42",
        status_message_id=9,
        status=NewsTaskStatus.FAILED,
        stage="failed",
        stage_message="Обработка завершилась ошибкой",
        error_message="bad <payload>",
    )

    await notifier.failure(task)

    call = bot.edit_message_text.await_args
    assert "bad &lt;payload&gt;" in call.args[0]
    assert call.kwargs["reply_markup"] is None


async def test_completion_sends_preview_and_editable_draft_controls():
    post = SourcePost(
        provider="news-manual",
        source_id="manual-1",
        source_url="",
        normalized_url="manual:manual-1",
        title="Новость",
        author_name="Ручной ввод",
        author_url="",
        media_items=[],
        metadata={"warnings": ["Проверить <факт>"]},
    )
    job = SimpleNamespace(
        id=12,
        post_data=serialize_post(post),
        channel=SimpleNamespace(alias="news"),
    )
    notifier, bot, previews = make_notifier(job=job)
    completed = CompletedNewsTask(7, 12, "42", 10)

    await notifier.complete(completed)

    bot.edit_message_text.assert_awaited_once()
    previews.send.assert_awaited_once_with(job, "42")
    final_call = bot.send_message.await_args_list[-1]
    assert "Черновик новости #12" in final_call.args[1]
    assert "Проверить &lt;факт&gt;" in final_call.args[1]
    assert final_call.kwargs["reply_markup"].inline_keyboard


async def test_completion_reports_preview_failure_but_keeps_draft_controls():
    post = SourcePost(
        provider="news-manual",
        source_id="manual-2",
        source_url="",
        normalized_url="manual:manual-2",
        title="Новость",
        author_name="Ручной ввод",
        author_url="",
        media_items=[],
    )
    job = SimpleNamespace(
        id=13,
        post_data=serialize_post(post),
        channel=SimpleNamespace(alias="news"),
    )
    notifier, bot, previews = make_notifier(job=job)
    previews.send.side_effect = RuntimeError("preview unavailable")

    await notifier.complete(CompletedNewsTask(8, 13, "42", None))

    assert bot.send_message.await_count == 2
    assert "показать предпросмотр не удалось" in bot.send_message.await_args_list[0].args[1]
    assert "Предпросмотр не отправлен" in bot.send_message.await_args_list[1].args[1]
