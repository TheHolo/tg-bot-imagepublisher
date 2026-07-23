import logging
from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.menu import (
    BACK_CALLBACK,
    CHANNEL_CALLBACK_PREFIX,
    CHANNELS_BUTTON,
    HEALTH_BUTTON,
    HEALTH_CALLBACK_PREFIX,
    HELP_BUTTON,
    MAIN_MENU_TEXT,
    PREVIEW_BUTTON,
    PREVIEW_CALLBACK_PREFIX,
    QUEUE_BUTTON,
    QUEUE_CALLBACK_PREFIX,
    STATS_BUTTON,
    back_menu_keyboard,
    channels_menu_keyboard,
    health_menu_keyboard,
    main_menu_keyboard,
    preview_menu_keyboard,
    queue_menu_keyboard,
    render_help,
)
from app.bot.states import EditPreview
from app.domain.enums import JobStatus
from app.domain.exceptions import ApplicationError
from app.services.ingest_service import IngestService
from app.services.health_service import HealthService, render_health_report
from app.services.job_service import JobService
from app.services.preview_service import PreviewService
from app.services.translation_service import TranslationService
from app.utils.tags import hashtags, merge_tags, normalize_tags
from app.utils.durations import format_duration, parse_duration
from app.utils.queue_schedule import estimate_queue_schedule, format_countdown, next_queued_by_schedule
from app.utils.text import provider_label

logger = logging.getLogger(__name__)


def preview_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Опубликовать", callback_data=f"publish:{job_id}")],
        [InlineKeyboardButton(text="Изменить теги", callback_data=f"tags:{job_id}"),
         InlineKeyboardButton(text="Сменить канал", callback_data=f"channel:{job_id}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"cancel:{job_id}")],
    ])


def duplicate_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Опубликовать повторно", callback_data=f"repeat:{job_id}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"cancel:{job_id}")],
    ])


def queued_preview_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Опубликовать сейчас", callback_data=f"preview_publish:{job_id}")],
        [InlineKeyboardButton(text="Заменить теги", callback_data=f"preview_tags_replace:{job_id}"),
         InlineKeyboardButton(text="Добавить теги", callback_data=f"preview_tags_add:{job_id}")],
        [InlineKeyboardButton(text="Отменить публикацию", callback_data=f"preview_cancel:{job_id}")],
    ])


def cancel_tag_input_keyboard(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отмена ввода", callback_data=f"tags_input_cancel:{job_id}")],
    ])


def channel_selection_keyboard(job_id: int, channels, current_channel_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{'✓ ' if channel.id == current_channel_id else ''}{channel.alias} — {channel.title}"[:64],
            callback_data=f"channel_select:{job_id}:{channel.id}",
        )]
        for channel in channels
    ]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"channel_select_cancel:{job_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def replace_channel_line(text: str, alias: str) -> str:
    lines = text.splitlines()
    return "\n".join(f"Канал: {alias}" if line.startswith("Канал: ") else line for line in lines)


def queue_summary_line(post_count: int, completion: datetime, now: datetime) -> str:
    return f"Всего постов: {post_count} · Вся очередь: {format_countdown(completion, now)}"


def build_router(
    ingest: IngestService, jobs: JobService, previews: PreviewService,
    translator: TranslationService, health: HealthService, wakeup, registry, settings,
) -> Router:
    router = Router()

    def effective_tags(user_tags: list[str], source_tags: list[str]) -> list[str]:
        if not settings.auto_add_source_tags:
            return user_tags
        return merge_tags(user_tags, source_tags, settings.max_tags, settings.max_tag_length)

    async def menu_callback_message(callback: CallbackQuery) -> Message | None:
        if isinstance(callback.message, Message):
            return callback.message
        await callback.answer("Сообщение меню больше недоступно.", show_alert=True)
        return None

    @router.message(Command("start", "menu"))
    async def main_menu(message: Message) -> None:
        await message.answer(MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())

    async def show_help(message: Message) -> None:
        await message.answer(
            render_help(), parse_mode="HTML", reply_markup=back_menu_keyboard(),
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await show_help(message)

    @router.message(F.text == HELP_BUTTON)
    async def help_button(message: Message) -> None:
        await show_help(message)

    @router.callback_query(F.data == BACK_CALLBACK)
    async def return_to_main_menu(callback: CallbackQuery) -> None:
        message = await menu_callback_message(callback)
        if message is None:
            return
        await callback.answer()
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await message.answer(MAIN_MENU_TEXT, reply_markup=main_menu_keyboard())

    @router.message(Command("providers"))
    async def providers(message: Message) -> None:
        await message.answer("Поддерживаемые источники: " + ", ".join(registry.names))

    @router.message(Command("channels"))
    async def channels(message: Message) -> None:
        rows = await jobs.channels()
        await message.answer("\n".join(
            f"{item.alias} — {item.title}{' (резервный)' if item.is_default else ''}; "
            f"интервал: {format_duration(item.publish_interval_seconds)}"
            for item in rows
        ) or "Каналы не настроены.")

    @router.message(F.text == CHANNELS_BUTTON)
    async def channels_button(message: Message) -> None:
        rows = await jobs.channels()
        await message.answer(
            "📡 Зарегистрированные каналы\n🟢 активен · ⚪ отключён",
            reply_markup=channels_menu_keyboard(rows),
        )

    @router.callback_query(F.data.startswith(CHANNEL_CALLBACK_PREFIX))
    async def registered_channel_noop(callback: CallbackQuery) -> None:
        await callback.answer()

    @router.message(Command("channel_interval"))
    async def channel_interval(message: Message, command: CommandObject) -> None:
        parts = (command.args or "").split()
        if len(parts) != 2:
            await message.answer("Использование: /channel_interval <alias> <30s|15m|2h|1d|0>")
            return
        alias = parts[0].strip().lower()
        try:
            seconds = parse_duration(parts[1])
        except ValueError as error:
            await message.answer(str(error))
            return
        channel = await jobs.set_channel_interval(alias, seconds)
        if not channel:
            await message.answer("Канал не найден или отключён.")
            return
        wakeup.set()
        await message.answer(f"Интервал канала {escape(alias)}: {format_duration(seconds)}.")

    @router.message(Command("status"))
    async def status(message: Message, command: CommandObject) -> None:
        if not command.args or not command.args.isdigit():
            await message.answer("Использование: /status <job_id>")
            return
        job = await jobs.get(int(command.args))
        await message.answer(f"Задание #{job.id}: {job.status}" if job else "Задание не найдено.")

    async def queue_text(alias: str | None) -> str:
        # The display limit must be applied after schedules from all channels
        # are merged. Limiting the database query here can hide whole channels
        # because JobService.queue orders rows by channel alias.
        rows = await jobs.queue(alias, limit=None)
        if rows is None:
            return f"Канал {escape(alias or '')} не найден или отключён."
        if not rows:
            return (
                f"Очередь канала {escape(alias)} пуста."
                if alias else "Очередь пуста."
            )
        now = datetime.now(timezone.utc)
        schedule = estimate_queue_schedule(rows, now)
        lines = [
            f"#{job.id} · {job.status} · {escape(job.channel.alias)} · {format_countdown(estimate, now)}"
            f"{' · вручную' if job.force_publish else ''}"
            for job, estimate in schedule[:50]
        ]
        if alias:
            completion = max(estimate for _, estimate in schedule)
            lines.insert(0, queue_summary_line(len(rows), completion, now))
        if len(schedule) > 50:
            lines.append(f"…показаны ближайшие 50 из {len(schedule)} заданий.")
        return "\n".join(lines)

    @router.message(Command("queue"))
    async def queue(message: Message, command: CommandObject) -> None:
        alias = (command.args or "").strip().lower() or None
        if alias and len(alias.split()) != 1:
            await message.answer("Использование: /queue [alias]")
            return
        await message.answer(await queue_text(alias))

    @router.message(F.text == QUEUE_BUTTON)
    async def queue_button(message: Message) -> None:
        channels = await jobs.channels()
        await message.answer(
            "📋 Выберите общую очередь или активный канал:",
            reply_markup=queue_menu_keyboard(channels),
        )

    @router.callback_query(F.data.startswith(QUEUE_CALLBACK_PREFIX))
    async def queue_menu_selection(callback: CallbackQuery) -> None:
        message = await menu_callback_message(callback)
        if message is None or callback.data is None:
            return
        selection = callback.data.removeprefix(QUEUE_CALLBACK_PREFIX)
        channels = await jobs.channels()
        alias = None
        if selection != "all":
            if not selection.isdigit():
                await callback.answer("Некорректный пункт меню.", show_alert=True)
                return
            channel = next(
                (item for item in channels if item.id == int(selection) and item.is_enabled),
                None,
            )
            if channel is None:
                await callback.answer("Канал удалён или отключён.", show_alert=True)
                return
            alias = channel.alias
        await callback.answer()
        text = await queue_text(alias)
        try:
            await message.edit_text(text, reply_markup=queue_menu_keyboard(channels))
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise

    @router.message(Command("publish"))
    async def publish_job(message: Message, command: CommandObject) -> None:
        if command.args and not command.args.isdigit():
            await message.answer("Использование: /publish [job_id]")
            return
        job = (
            await jobs.force_publish(int(command.args))
            if command.args
            else await jobs.force_next_publish()
        )
        if not job:
            await message.answer(
                "Ручная публикация доступна только для задания со статусом queued."
                if command.args else "В очереди нет заданий для публикации."
            )
            return
        wakeup.set()
        await message.answer(
            f"Задание #{job.id} поставлено на ручную публикацию. "
            "После публикации интервал канала начнётся заново."
        )

    async def show_preview(message: Message, argument: str) -> None:
        if argument and len(argument.split()) != 1:
            await message.answer("Использование: /preview [job_id|alias]")
            return
        estimate = None
        if argument.isdigit():
            job = await jobs.get(int(argument))
        else:
            alias = argument or None
            rows = await jobs.queue(alias, limit=None)
            if rows is None:
                await message.answer(f"Канал {escape(alias or '')} не найден или отключён.")
                return
            scheduled = next_queued_by_schedule(rows)
            job, estimate = scheduled if scheduled else (None, None)
        if not job:
            await message.answer(
                "Задание не найдено."
                if argument.isdigit()
                else (
                    f"В очереди канала {escape(argument)} нет заданий для предпросмотра."
                    if argument
                    else "В очереди нет заданий для предпросмотра."
                )
            )
            return
        if job.status != JobStatus.QUEUED:
            await message.answer(f"Задание #{job.id} уже не находится в очереди.")
            return
        try:
            await previews.send(job, message.chat.id)
        except ApplicationError as error:
            await message.answer(f"Не удалось подготовить предпросмотр задания #{job.id}: {error}")
            return
        await message.answer(
            f"Предпросмотр задания #{job.id} · {escape(job.channel.alias)}"
            f"{' · ' + format_countdown(estimate) if estimate else ''}. "
            "Очередь и интервал не изменены.",
            reply_markup=queued_preview_keyboard(job.id),
        )

    @router.message(Command("preview"))
    async def preview_job(message: Message, command: CommandObject) -> None:
        await show_preview(message, (command.args or "").strip().lower())

    @router.message(F.text == PREVIEW_BUTTON)
    async def preview_button(message: Message) -> None:
        channels = await jobs.channels()
        await message.answer(
            "🖼 Выберите активный канал или ближайший пост во всей очереди:",
            reply_markup=preview_menu_keyboard(channels),
        )

    @router.callback_query(F.data.startswith(PREVIEW_CALLBACK_PREFIX))
    async def preview_menu_selection(callback: CallbackQuery) -> None:
        message = await menu_callback_message(callback)
        if message is None or callback.data is None:
            return
        selection = callback.data.removeprefix(PREVIEW_CALLBACK_PREFIX)
        argument = ""
        if selection != "next":
            if not selection.isdigit():
                await callback.answer("Некорректный пункт меню.", show_alert=True)
                return
            channels = await jobs.channels()
            channel = next(
                (item for item in channels if item.id == int(selection) and item.is_enabled),
                None,
            )
            if channel is None:
                await callback.answer("Канал удалён или отключён.", show_alert=True)
                return
            argument = channel.alias
        await callback.answer("Подготавливаю предпросмотр…")
        target = f"канал {argument}" if argument else "вся очередь"
        await message.edit_text(f"⏳ Ищу ближайший пост · {target}")
        await show_preview(message, argument)

    @router.callback_query(F.data.startswith("preview_publish:"))
    async def publish_from_preview(callback: CallbackQuery, state: FSMContext) -> None:
        job_id = int(callback.data.rsplit(":", 1)[1])
        job = await jobs.force_publish(job_id)
        if not job:
            await callback.answer("Задание уже не находится в очереди.", show_alert=True)
            return
        await state.clear()
        wakeup.set()
        await callback.message.edit_text(
            f"Задание #{job_id} поставлено на публикацию сейчас. "
            "После отправки интервал канала начнётся заново."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("preview_tags_replace:") | F.data.startswith("preview_tags_add:"))
    async def edit_queued_tags(callback: CallbackQuery, state: FSMContext) -> None:
        action, raw_job_id = callback.data.rsplit(":", 1)
        job_id = int(raw_job_id)
        job = await jobs.get(job_id)
        if not job or job.status != JobStatus.QUEUED:
            await callback.answer("Задание уже не находится в очереди.", show_alert=True)
            return
        mode = "add" if action == "preview_tags_add" else "replace"
        await state.set_state(EditPreview.waiting_for_tags)
        await state.set_data({
            "job_id": job_id,
            "tag_context": "queued_preview",
            "tag_mode": mode,
            "control_chat_id": callback.message.chat.id,
            "control_message_id": callback.message.message_id,
        })
        prompt = (
            "Введите дополнительные пользовательские теги через пробел. "
            "Они будут добавлены перед текущими."
            if mode == "add"
            else "Введите новый список пользовательских тегов через пробел. Текущие теги будут заменены."
        )
        await callback.message.edit_text(prompt, reply_markup=cancel_tag_input_keyboard(job_id))
        await callback.answer()

    @router.callback_query(F.data.startswith("preview_cancel:"))
    async def cancel_from_preview(callback: CallbackQuery, state: FSMContext) -> None:
        job_id = int(callback.data.rsplit(":", 1)[1])
        if not await jobs.request_cancel(job_id):
            await callback.answer("Задание уже нельзя отменить.", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_text(f"Публикация задания #{job_id} отменена и убрана из очереди.")
        await callback.answer()

    @router.message(Command("recent"))
    async def recent(message: Message) -> None:
        rows = await jobs.recent()
        await message.answer("\n".join(f"#{job.id} · {job.status} · {job.provider}" for job in rows) or "История пуста.")

    async def show_stats(message: Message) -> None:
        values = await jobs.stats()
        await message.answer("\n".join(f"{key}: {value}" for key, value in sorted(values.items())) or "Заданий пока нет.")

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        await show_stats(message)

    @router.message(F.text == STATS_BUTTON)
    async def stats_button(message: Message) -> None:
        await show_stats(message)

    async def show_health(message: Message, *, full: bool) -> None:
        report = await health.check(full=full)
        await message.answer(
            render_health_report(report),
            parse_mode="HTML",
            reply_markup=health_menu_keyboard(),
        )

    @router.message(Command("health"))
    async def health_status(message: Message, command: CommandObject) -> None:
        argument = (command.args or "").strip().lower()
        if argument not in {"", "full"}:
            await message.answer("Использование: /health [full]")
            return
        await show_health(message, full=argument == "full")

    @router.message(F.text == HEALTH_BUTTON)
    async def health_button(message: Message) -> None:
        await show_health(message, full=False)

    @router.callback_query(F.data.startswith(HEALTH_CALLBACK_PREFIX))
    async def health_menu_selection(callback: CallbackQuery) -> None:
        message = await menu_callback_message(callback)
        if message is None or callback.data is None:
            return
        selection = callback.data.removeprefix(HEALTH_CALLBACK_PREFIX)
        if selection not in {"refresh", "full"}:
            await callback.answer("Некорректный пункт меню.", show_alert=True)
            return
        full = selection == "full"
        await callback.answer("Выполняю полную проверку…" if full else "Обновляю…")
        if full:
            await message.edit_text("⏳ Выполняю полную проверку здоровья бота…")
        report = await health.check(full=full)
        try:
            await message.edit_text(
                render_health_report(report),
                parse_mode="HTML",
                reply_markup=health_menu_keyboard(),
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise

    @router.message(Command("cancel", "retry"))
    async def control(message: Message, command: CommandObject) -> None:
        if not command.args or not command.args.isdigit():
            await message.answer(f"Использование: /{command.command} <job_id>")
            return
        job_id = int(command.args)
        ok = await jobs.request_cancel(job_id) if command.command == "cancel" else await jobs.enqueue(job_id)
        if ok and command.command == "retry":
            wakeup.set()
        await message.answer("Готово." if ok else "Операция недоступна для этого задания.")

    @router.callback_query(F.data.startswith("publish:"))
    async def publish(callback: CallbackQuery) -> None:
        job_id = int(callback.data.split(":", 1)[1])
        if await jobs.enqueue(job_id):
            wakeup.set()
            await callback.message.edit_text(f"Задание #{job_id} добавлено в очередь.")
        else:
            await callback.answer("Задание уже обработано.", show_alert=True)
        await callback.answer()

    @router.callback_query(F.data.startswith("repeat:"))
    async def repeat(callback: CallbackQuery) -> None:
        job_id = int(callback.data.split(":", 1)[1])
        if await jobs.allow_duplicate_and_enqueue(job_id):
            wakeup.set()
            await callback.message.edit_text(f"Повторная публикация #{job_id} подтверждена и добавлена в очередь.")
        else:
            await callback.answer("Задание уже обработано.", show_alert=True)
        await callback.answer()

    @router.callback_query(F.data.startswith("cancel:"))
    async def cancel(callback: CallbackQuery) -> None:
        job_id = int(callback.data.split(":", 1)[1])
        await jobs.request_cancel(job_id)
        await callback.message.edit_text(f"Задание #{job_id} отменено.")
        await callback.answer()

    @router.callback_query(F.data.startswith("tags:"))
    async def edit_tags(callback: CallbackQuery, state: FSMContext) -> None:
        job_id = int(callback.data.split(":", 1)[1])
        await state.set_state(EditPreview.waiting_for_tags)
        await state.set_data({"job_id": job_id, "tag_context": "initial_preview", "tag_mode": "replace"})
        await callback.message.answer(
            "Отправьте новый список тегов через пробел.",
            reply_markup=cancel_tag_input_keyboard(job_id),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("tags_input_cancel:"))
    async def cancel_tag_input(callback: CallbackQuery, state: FSMContext) -> None:
        job_id = int(callback.data.rsplit(":", 1)[1])
        data = await state.get_data()
        await state.clear()
        keyboard = (
            queued_preview_keyboard(job_id)
            if data.get("tag_context") == "queued_preview"
            else preview_keyboard(job_id)
        )
        await callback.message.edit_text("Ввод тегов отменён.", reply_markup=keyboard)
        await callback.answer()

    @router.callback_query(F.data.startswith("channel:"))
    async def edit_channel(callback: CallbackQuery, state: FSMContext) -> None:
        job_id = int(callback.data.split(":", 1)[1])
        job = await jobs.get(job_id)
        if not job or job.status != JobStatus.WAITING_CONFIRMATION:
            await callback.answer("Предпросмотр уже недоступен.", show_alert=True)
            return
        channels = [channel for channel in await jobs.channels() if channel.is_enabled]
        if not channels:
            await callback.answer("Нет доступных каналов.", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_reply_markup(
            reply_markup=channel_selection_keyboard(job_id, channels, job.target_channel_id)
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("channel_select:"))
    async def select_channel(callback: CallbackQuery) -> None:
        _, raw_job_id, raw_channel_id = callback.data.split(":", 2)
        job_id, channel_id = int(raw_job_id), int(raw_channel_id)
        channel = await jobs.change_channel(job_id, channel_id)
        if not channel:
            await callback.answer("Канал недоступен или предпросмотр уже закрыт.", show_alert=True)
            return
        updated_text = replace_channel_line(callback.message.text or "", channel.alias)
        await callback.message.edit_text(updated_text, reply_markup=preview_keyboard(job_id))
        await callback.answer(f"Выбран канал: {channel.alias}")

    @router.callback_query(F.data.startswith("channel_select_cancel:"))
    async def cancel_channel_selection(callback: CallbackQuery) -> None:
        job_id = int(callback.data.rsplit(":", 1)[1])
        await callback.message.edit_reply_markup(reply_markup=preview_keyboard(job_id))
        await callback.answer()

    @router.message(EditPreview.waiting_for_tags)
    async def receive_tags(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        job = await jobs.get(data["job_id"])
        expected_status = (
            JobStatus.QUEUED if data.get("tag_context") == "queued_preview"
            else JobStatus.WAITING_CONFIRMATION
        )
        if not job or job.status != expected_status:
            await state.clear()
            await message.answer("Предпросмотр уже недоступен.")
            return
        tags = normalize_tags((message.text or "").split(), settings.max_tags, settings.max_tag_length)
        if data.get("tag_mode") == "add":
            tags = merge_tags(tags, job.user_tags, settings.max_tags, settings.max_tag_length)
        async with jobs.sessions() as session, session.begin():
            stored = await session.get(type(job), job.id)
            stored.user_tags = tags
        await state.clear()
        combined = effective_tags(tags, job.source_tags)
        if data.get("tag_context") != "queued_preview":
            await message.answer(
                f"Теги обновлены: {hashtags(combined) or 'без тегов'}",
                reply_markup=preview_keyboard(job.id),
            )
            return
        try:
            await message.bot.edit_message_text(
                f"Теги задания #{job.id} обновлены. Ниже показан новый предпросмотр.",
                chat_id=data["control_chat_id"], message_id=data["control_message_id"],
            )
        except Exception:
            logger.debug("preview_control_edit_failed job_id=%s", job.id, exc_info=True)
        refreshed = await jobs.get(job.id)
        try:
            await previews.send(refreshed, message.chat.id)
        except ApplicationError as error:
            await message.answer(
                f"Теги сохранены, но обновить предпросмотр не удалось: {error}",
                reply_markup=queued_preview_keyboard(job.id),
            )
            return
        await message.answer(
            f"Теги обновлены: {hashtags(combined) or 'без тегов'}",
            reply_markup=queued_preview_keyboard(job.id),
        )

    @router.message(F.text)
    async def submission(message: Message) -> None:
        user = await jobs.ensure_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
        try:
            urls, tags, alias = ingest.parse(message.text)
            channel = (
                await jobs.get_channel(alias)
                if alias
                else await jobs.get_preferred_channel(user.id, settings.default_channel_alias)
            )
            if not channel:
                await message.answer("Канал не найден или отключён. Проверьте --channel и CHANNELS_JSON.")
                return
            failures: list[tuple[str, str]] = []
            for position, url in enumerate(urls, start=1):
                try:
                    post = await ingest.fetch(url)
                    await translator.enrich_title(post)
                except ApplicationError as error:
                    failures.append((url, str(error)))
                    continue
                except Exception as error:
                    logger.error(
                        "batch_ingest_failed url=%s", url,
                        exc_info=(type(error), error, error.__traceback__),
                    )
                    failures.append((url, "Не удалось получить публикацию"))
                    continue
                job = await jobs.create_preview(user.id, post, channel.id, tags, settings.max_job_attempts)
                combined_tags = effective_tags(tags, post.source_tags)
                prefix = f"Ссылка {position}/{len(urls)}\n\n" if len(urls) > 1 else ""
                duplicate_state = await jobs.duplicate_state_for(
                    post.provider, post.source_id, channel.id, job.id,
                )
                if duplicate_state:
                    duplicate_message = (
                        f"Эта публикация уже была отправлена в канал {escape(channel.alias)}. "
                        "Повторить публикацию?"
                        if duplicate_state == "published"
                        else f"Эта публикация уже ожидает обработки для канала {escape(channel.alias)}. "
                        "Добавить её повторно?"
                    )
                    await message.answer(
                        prefix + duplicate_message,
                        reply_markup=duplicate_keyboard(job.id),
                    )
                    continue
                await message.answer(
                    prefix + f"Источник: {escape(provider_label(post.provider))}\nАвтор: {escape(post.author_name)}\n"
                    f"Название: {escape(post.title)}\nФайлов: {len(post.media_items)}\nКанал: {escape(channel.alias)}\n\n"
                    f"Теги: {hashtags(combined_tags) or '—'}", reply_markup=preview_keyboard(job.id)
                )
            if failures:
                details = "\n".join(f"• {escape(url)} — {escape(reason)}" for url, reason in failures)
                await message.answer(f"Не удалось обработать {len(failures)} из {len(urls)} ссылок:\n{details}")
        except ApplicationError as error:
            await message.answer(str(error))
        except Exception:
            await message.answer("Не удалось получить публикацию. Подробности записаны в лог.")
            raise

    return router
