from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.types import FSInputFile, InputMediaDocument, InputMediaPhoto

from app.db.models import Channel, Job
from app.domain.exceptions import ChannelPermissionError, PublishError
from app.domain.models import PreparedMedia, PublicationResult, SourcePost


class TelegramPublisher:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def publish(self, job: Job, post: SourcePost, media: list[PreparedMedia], channel: Channel, caption: str) -> PublicationResult:
        message_ids: list[int] = []
        try:
            if len(media) == 1:
                item = media[0]
                if item.as_document:
                    message = await self.bot.send_document(channel.telegram_chat_id, FSInputFile(item.path), caption=caption, parse_mode="HTML")
                else:
                    message = await self.bot.send_photo(channel.telegram_chat_id, FSInputFile(item.path), caption=caption, parse_mode="HTML")
                message_ids.append(message.message_id)
            else:
                for offset in range(0, len(media), 10):
                    group = []
                    for index, item in enumerate(media[offset : offset + 10]):
                        kwargs = {"media": FSInputFile(item.path)}
                        if offset == 0 and index == 0:
                            kwargs.update(caption=caption, parse_mode="HTML")
                        group.append(InputMediaDocument(**kwargs) if item.as_document else InputMediaPhoto(**kwargs))
                    messages = await self.bot.send_media_group(channel.telegram_chat_id, group)
                    message_ids.extend(message.message_id for message in messages)
        except TelegramForbiddenError as error:
            raise ChannelPermissionError("Бот не имеет прав на публикацию в канале") from error
        except (TelegramBadRequest, TelegramNetworkError) as error:
            raise PublishError(str(error)) from error
        return PublicationResult(str(channel.telegram_chat_id), message_ids, datetime.now(timezone.utc), len(media))
