from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.states import EditPreview
from app.domain.enums import JobStatus
from app.domain.exceptions import ApplicationError
from app.services.ingest_service import IngestService
from app.services.job_service import JobService
from app.utils.tags import hashtags, normalize_tags


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


def build_router(ingest: IngestService, jobs: JobService, wakeup, registry, settings) -> Router:
    router = Router()

    @router.message(Command("start", "help"))
    async def help_message(message: Message) -> None:
        await message.answer(
            "Отправьте ссылку Pixiv или прямую ссылку на изображение и теги.\n"
            "Пример: https://www.pixiv.net/en/artworks/123 art landscape --channel artwork\n\n"
            "Команды: /status ID, /queue, /cancel ID, /retry ID, /recent, /channels, /providers, /stats, /health"
        )

    @router.message(Command("providers"))
    async def providers(message: Message) -> None:
        await message.answer("Поддерживаемые источники: " + ", ".join(registry.names))

    @router.message(Command("channels"))
    async def channels(message: Message) -> None:
        rows = await jobs.channels()
        await message.answer("\n".join(f"{item.alias} — {item.title}{' (по умолчанию)' if item.is_default else ''}" for item in rows) or "Каналы не настроены.")

    @router.message(Command("status"))
    async def status(message: Message, command: CommandObject) -> None:
        if not command.args or not command.args.isdigit():
            await message.answer("Использование: /status <job_id>")
            return
        job = await jobs.get(int(command.args))
        await message.answer(f"Задание #{job.id}: {job.status}" if job else "Задание не найдено.")

    @router.message(Command("queue"))
    async def queue(message: Message) -> None:
        rows = await jobs.queue()
        await message.answer("\n".join(f"#{job.id} · {job.status} · {job.provider}" for job in rows) or "Очередь пуста.")

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
        await state.update_data(job_id=job_id)
        await callback.message.answer("Отправьте новый список тегов через пробел.")
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
        if not job or job.status != JobStatus.WAITING_CONFIRMATION:
            await state.clear()
            await message.answer("Предпросмотр уже недоступен.")
            return
        tags = normalize_tags((message.text or "").split(), settings.max_tags, settings.max_tag_length)
        async with jobs.sessions() as session, session.begin():
            stored = await session.get(type(job), job.id)
            stored.user_tags = tags
        await state.clear()
        await message.answer(f"Теги обновлены: {hashtags(tags) or 'без тегов'}", reply_markup=preview_keyboard(job.id))

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
            post, tags, alias, ignored = await ingest.ingest(message.text)
            channel = await jobs.get_channel(alias or settings.default_channel_alias)
            if not channel:
                await message.answer("Канал не найден или отключён. Проверьте --channel и CHANNELS_JSON.")
                return
            job = await jobs.create_preview(user.id, post, channel.id, tags, settings.max_job_attempts)
            duplicate = await jobs.duplicate_for(post.provider, post.source_id, channel.id)
            if duplicate:
                await message.answer(
                    f"Эта публикация уже была отправлена в канал {escape(channel.alias)}. Повторить публикацию?",
                    reply_markup=duplicate_keyboard(job.id),
                )
                return
            warning = f"\n⚠️ Ещё ссылок проигнорировано: {ignored}" if ignored else ""
            await message.answer(
                f"Источник: {escape(post.provider.title())}\nАвтор: {escape(post.author_name)}\n"
                f"Название: {escape(post.title)}\nФайлов: {len(post.media_items)}\nКанал: {escape(channel.alias)}\n\n"
                f"Теги: {hashtags(tags) or '—'}{warning}", reply_markup=preview_keyboard(job.id)
            )
        except ApplicationError as error:
            await message.answer(str(error))
        except Exception:
            await message.answer("Не удалось получить публикацию. Подробности записаны в лог.")
            raise

    return router
