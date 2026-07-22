from html import escape
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.states import EditPreview
from app.domain.enums import JobStatus
from app.domain.exceptions import ApplicationError
from app.services.ingest_service import IngestService
from app.services.job_service import JobService
from app.services.preview_service import PreviewService
from app.services.translation_service import TranslationService
from app.utils.tags import hashtags, merge_tags, normalize_tags
from app.utils.durations import format_duration, parse_duration
from app.utils.queue_schedule import estimate_queue_schedule, format_countdown

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


def build_router(
    ingest: IngestService, jobs: JobService, previews: PreviewService,
    translator: TranslationService, wakeup, registry, settings,
) -> Router:
    router = Router()

    def effective_tags(user_tags: list[str], source_tags: list[str]) -> list[str]:
        if not settings.auto_add_source_tags:
            return user_tags
        return merge_tags(user_tags, source_tags, settings.max_tags, settings.max_tag_length)

    @router.message(Command("start", "help"))
    async def help_message(message: Message) -> None:
        await message.answer(
            "Отправьте ссылку Pixiv или прямую ссылку на изображение и теги.\n"
            "Пример: https://www.pixiv.net/en/artworks/123 art landscape --channel artwork\n\n"
            "Команды: /status ID, /queue [ALIAS], /preview [ID], /publish [ID], /cancel ID, /retry ID, /recent, /channels, "
            "/channel_interval ALIAS INTERVAL, /providers, /stats, /health"
        )

    @router.message(Command("providers"))
    async def providers(message: Message) -> None:
        await message.answer("Поддерживаемые источники: " + ", ".join(registry.names))

    @router.message(Command("channels"))
    async def channels(message: Message) -> None:
        rows = await jobs.channels()
        await message.answer("\n".join(
            f"{item.alias} — {item.title}{' (по умолчанию)' if item.is_default else ''}; "
            f"интервал: {format_duration(item.publish_interval_seconds)}"
            for item in rows
        ) or "Каналы не настроены.")

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

    @router.message(Command("queue"))
    async def queue(message: Message, command: CommandObject) -> None:
        alias = (command.args or "").strip().lower() or None
        if alias and len(alias.split()) != 1:
            await message.answer("Использование: /queue [alias]")
            return
        rows = await jobs.queue(alias)
        if rows is None:
            await message.answer(f"Канал {escape(alias)} не найден или отключён.")
            return
        if not rows:
            await message.answer(
                f"Очередь канала {escape(alias)} пуста." if alias else "Очередь пуста."
            )
            return
        await message.answer("\n".join(
            f"#{job.id} · {job.status} · {escape(job.channel.alias)} · {format_countdown(estimate)}"
            f"{' · вручную' if job.force_publish else ''}"
            for job, estimate in estimate_queue_schedule(rows)
        ))

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

    @router.message(Command("preview"))
    async def preview_job(message: Message, command: CommandObject) -> None:
        if command.args and not command.args.isdigit():
            await message.answer("Использование: /preview [job_id]")
            return
        job = await jobs.get(int(command.args)) if command.args else await jobs.next_queued()
        if not job:
            await message.answer(
                "Задание не найдено." if command.args else "В очереди нет заданий для предпросмотра."
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
            f"Предпросмотр задания #{job.id}. Очередь и интервал не изменены.",
            reply_markup=queued_preview_keyboard(job.id),
        )

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

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        values = await jobs.stats()
        await message.answer("\n".join(f"{key}: {value}" for key, value in sorted(values.items())) or "Заданий пока нет.")

    @router.message(Command("health"))
    async def health(message: Message) -> None:
        await jobs.stats()
        await message.answer("База данных: OK\nWorker: OK\nTelegram API: OK")

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
        await state.set_state(EditPreview.waiting_for_channel)
        await state.update_data(job_id=job_id)
        aliases = ", ".join(channel.alias for channel in await jobs.channels() if channel.is_enabled)
        await callback.message.answer(f"Отправьте alias канала. Доступны: {aliases}")
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

    @router.message(EditPreview.waiting_for_channel)
    async def receive_channel(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        alias = (message.text or "").strip().lower()
        if not await jobs.change_channel(data["job_id"], alias):
            await message.answer("Канал не найден или предпросмотр уже недоступен. Попробуйте ещё раз.")
            return
        await state.clear()
        await message.answer(f"Канал изменён на {escape(alias)}.", reply_markup=preview_keyboard(data["job_id"]))

    @router.message(F.text)
    async def submission(message: Message) -> None:
        user = await jobs.ensure_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
        try:
            urls, tags, alias = ingest.parse(message.text)
            channel = await jobs.get_channel(alias or settings.default_channel_alias)
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
                duplicate = await jobs.duplicate_for(post.provider, post.source_id, channel.id)
                if duplicate:
                    await message.answer(
                        prefix + f"Эта публикация уже была отправлена в канал {escape(channel.alias)}. Повторить публикацию?",
                        reply_markup=duplicate_keyboard(job.id),
                    )
                    continue
                await message.answer(
                    prefix + f"Источник: {escape(post.provider.title())}\nАвтор: {escape(post.author_name)}\n"
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
