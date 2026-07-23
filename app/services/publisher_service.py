from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.types import FSInputFile, InputMediaDocument, InputMediaPhoto

from app.db.models import Channel, Job
from app.domain.exceptions import ChannelPermissionError, PublishError, UncertainPublishError
from app.domain.models import PreparedMedia, PublicationResult, SourcePost


class TelegramPublisher:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def publish(self, job: Job, post: SourcePost, media: list[PreparedMedia], channel: Channel, caption: str) -> PublicationResult:
        return await self._send(channel.telegram_chat_id, media, caption)

    async def preview(self, chat_id: int | str, media: list[PreparedMedia], caption: str) -> PublicationResult:
        return await self._send(chat_id, media, caption)

    async def _send(self, chat_id: int | str, media: list[PreparedMedia], caption: str) -> PublicationResult:
        if not media:
            raise PublishError("Публикация не содержит медиафайлов")
        message_ids: list[int] = []
        try:
            if len(media) == 1:
                item = media[0]
                if item.as_document:
                    message = await self.bot.send_document(chat_id, FSInputFile(item.path), caption=caption, parse_mode="HTML")
                else:
                    message = await self.bot.send_photo(chat_id, FSInputFile(item.path), caption=caption, parse_mode="HTML")
                message_ids.append(message.message_id)
            else:
                send_as_documents = any(item.as_document for item in media)
                for offset in range(0, len(media), 10):
                    chunk = media[offset : offset + 10]
                    chunk_caption = caption if offset == 0 else None
                    if len(chunk) == 1:
                        item = chunk[0]
                        if send_as_documents:
                            message = await self.bot.send_document(
                                chat_id, FSInputFile(item.path), caption=chunk_caption,
                                parse_mode="HTML" if chunk_caption else None,
                            )
                        else:
                            message = await self.bot.send_photo(
                                chat_id, FSInputFile(item.path), caption=chunk_caption,
                                parse_mode="HTML" if chunk_caption else None,
                            )
                        message_ids.append(message.message_id)
                        continue
                    group: list[Any] = []
                    for index, item in enumerate(chunk):
                        item_caption = chunk_caption if index == 0 else None
                        if send_as_documents:
                            group.append(InputMediaDocument(
                                media=FSInputFile(item.path), caption=item_caption,
                                parse_mode="HTML" if item_caption else None,
                            ))
                        else:
                            group.append(InputMediaPhoto(
                                media=FSInputFile(item.path), caption=item_caption,
                                parse_mode="HTML" if item_caption else None,
                            ))
                    messages = await self.bot.send_media_group(chat_id, group)
                    message_ids.extend(message.message_id for message in messages)
        except TelegramForbiddenError as error:
            raise ChannelPermissionError("Бот не имеет прав на публикацию в канале") from error
        except TelegramNetworkError as error:
            raise UncertainPublishError(
                "Ответ Telegram потерян; проверьте канал перед ручным повтором"
            ) from error
        except TelegramBadRequest as error:
            raise PublishError(str(error)) from error
        return PublicationResult(str(chat_id), message_ids, datetime.now(timezone.utc), len(media))
