import re
from datetime import UTC, datetime
from html import unescape
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
)
from aiogram.types import (
    FSInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)

from app.db.models import Channel, Job
from app.domain.enums import ContentKind, MediaType
from app.domain.exceptions import (
    ChannelPermissionError,
    PublishError,
    UncertainPublishError,
)
from app.domain.models import PreparedMedia, PublicationResult, SourcePost

_HTML_TAG = re.compile(r"<[^>]+>")


class TelegramPublisher:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def publish(self, job: Job, post: SourcePost, media: list[PreparedMedia], channel: Channel, caption: str) -> PublicationResult:
        return await self._send(channel.telegram_chat_id, media, caption, post.content_kind)

    async def preview(
        self, chat_id: int | str, media: list[PreparedMedia], caption: str,
        post: SourcePost | None = None,
    ) -> PublicationResult:
        kind = post.content_kind if post is not None else ContentKind.ARTWORK
        return await self._send(chat_id, media, caption, kind)

    async def _send(
        self, chat_id: int | str, media: list[PreparedMedia], caption: str,
        content_kind: ContentKind = ContentKind.ARTWORK,
    ) -> PublicationResult:
        if content_kind == ContentKind.NEWS:
            return await self._send_news(chat_id, media, caption)
        if not media:
            raise PublishError("Публикация не содержит медиафайлов")
        return await self._send_media(chat_id, media, caption)

    async def _send_news(
        self, chat_id: int | str, media: list[PreparedMedia], text: str,
    ) -> PublicationResult:
        media_result: PublicationResult | None = None
        try:
            if not media:
                message = await self.bot.send_message(chat_id, text, parse_mode="HTML")
                return PublicationResult(
                    str(chat_id), [message.message_id], datetime.now(UTC), 0,
                )
            if self._plain_length(text) <= 1024:
                return await self._send_media(chat_id, media, text)

            media_result = await self._send_media(chat_id, media, "")
            message = await self.bot.send_message(chat_id, text, parse_mode="HTML")
            return PublicationResult(
                str(chat_id), [*media_result.message_ids, message.message_id],
                datetime.now(UTC), len(media),
            )
        except (ChannelPermissionError, PublishError, UncertainPublishError):
            raise
        except TelegramForbiddenError as error:
            if media_result is not None and media_result.message_ids:
                raise self._partial_publish_error() from error
            raise ChannelPermissionError("Бот не имеет прав на публикацию в канале") from error
        except TelegramNetworkError as error:
            if media_result is not None and media_result.message_ids:
                raise self._partial_publish_error() from error
            raise UncertainPublishError(
                "Ответ Telegram потерян; проверьте канал перед ручным повтором"
            ) from error
        except TelegramBadRequest as error:
            if media_result is not None and media_result.message_ids:
                raise self._partial_publish_error() from error
            raise PublishError(str(error)) from error
        except TelegramAPIError as error:
            if media_result is not None and media_result.message_ids:
                raise self._partial_publish_error() from error
            raise PublishError(str(error)) from error

    async def _send_media(
        self, chat_id: int | str, media: list[PreparedMedia], caption: str,
    ) -> PublicationResult:
        message_ids: list[int] = []
        try:
            if len(media) == 1:
                item = media[0]
                payload = self._payload(item)
                if item.media_type == MediaType.VIDEO and not item.as_document:
                    message = await self.bot.send_video(
                        chat_id, payload, caption=caption or None,
                        parse_mode="HTML" if caption else None,
                    )
                elif item.media_type == MediaType.ANIMATION and not item.as_document:
                    message = await self.bot.send_animation(
                        chat_id, payload, caption=caption or None,
                        parse_mode="HTML" if caption else None,
                    )
                elif item.as_document or item.media_type == MediaType.DOCUMENT:
                    message = await self.bot.send_document(
                        chat_id, payload, caption=caption or None,
                        parse_mode="HTML" if caption else None,
                    )
                else:
                    message = await self.bot.send_photo(
                        chat_id, payload, caption=caption or None,
                        parse_mode="HTML" if caption else None,
                    )
                message_ids.append(message.message_id)
            else:
                has_documents = any(
                    item.as_document or item.media_type == MediaType.DOCUMENT for item in media
                )
                send_as_documents = has_documents and all(
                    not item.telegram_file_id
                    or item.as_document
                    or item.media_type == MediaType.DOCUMENT
                    for item in media
                )
                for offset in range(0, len(media), 10):
                    chunk = media[offset : offset + 10]
                    chunk_caption = caption if offset == 0 else None
                    can_group = self._can_group(chunk, send_as_documents)
                    if len(chunk) == 1 or not can_group:
                        if len(chunk) > 1 and not can_group:
                            for index, item in enumerate(chunk):
                                single_caption = chunk_caption if index == 0 else None
                                result = await self._send_media(
                                    chat_id, [item], single_caption or "",
                                )
                                message_ids.extend(result.message_ids)
                            continue
                        item = chunk[0]
                        payload = self._payload(item)
                        if item.media_type == MediaType.VIDEO and not send_as_documents:
                            message = await self.bot.send_video(
                                chat_id, payload, caption=chunk_caption,
                                parse_mode="HTML" if chunk_caption else None,
                            )
                        elif item.media_type == MediaType.ANIMATION and not send_as_documents:
                            message = await self.bot.send_animation(
                                chat_id, payload, caption=chunk_caption,
                                parse_mode="HTML" if chunk_caption else None,
                            )
                        elif send_as_documents:
                            message = await self.bot.send_document(
                                chat_id, payload, caption=chunk_caption,
                                parse_mode="HTML" if chunk_caption else None,
                            )
                        else:
                            message = await self.bot.send_photo(
                                chat_id, payload, caption=chunk_caption,
                                parse_mode="HTML" if chunk_caption else None,
                            )
                        message_ids.append(message.message_id)
                        continue
                    group: list[Any] = []
                    for index, item in enumerate(chunk):
                        item_caption = chunk_caption if index == 0 else None
                        if send_as_documents:
                            group.append(InputMediaDocument(
                                media=self._payload(item), caption=item_caption,
                                parse_mode="HTML" if item_caption else None,
                            ))
                        elif item.media_type == MediaType.VIDEO:
                            group.append(InputMediaVideo(
                                media=self._payload(item), caption=item_caption,
                                parse_mode="HTML" if item_caption else None,
                            ))
                        else:
                            group.append(InputMediaPhoto(
                                media=self._payload(item), caption=item_caption,
                                parse_mode="HTML" if item_caption else None,
                            ))
                    messages = await self.bot.send_media_group(chat_id, group)
                    message_ids.extend(message.message_id for message in messages)
        except UncertainPublishError:
            raise
        except (ChannelPermissionError, PublishError) as error:
            if message_ids:
                raise self._partial_publish_error() from error
            raise
        except TelegramForbiddenError as error:
            if message_ids:
                raise self._partial_publish_error() from error
            raise ChannelPermissionError("Бот не имеет прав на публикацию в канале") from error
        except TelegramNetworkError as error:
            raise UncertainPublishError(
                "Ответ Telegram потерян; проверьте канал перед ручным повтором"
            ) from error
        except TelegramBadRequest as error:
            if message_ids:
                raise self._partial_publish_error() from error
            raise PublishError(str(error)) from error
        except TelegramAPIError as error:
            if message_ids:
                raise self._partial_publish_error() from error
            raise PublishError(str(error)) from error
        return PublicationResult(str(chat_id), message_ids, datetime.now(UTC), len(media))

    @staticmethod
    def _payload(item: PreparedMedia) -> str | FSInputFile:
        if item.telegram_file_id:
            return item.telegram_file_id
        if item.path is None:
            raise PublishError("Медиафайл недоступен")
        return FSInputFile(item.path)

    @staticmethod
    def _can_group(items: list[PreparedMedia], as_documents: bool) -> bool:
        if as_documents:
            return True
        return all(item.media_type in {MediaType.IMAGE, MediaType.VIDEO} for item in items)

    @staticmethod
    def _plain_length(value: str) -> int:
        return len(unescape(_HTML_TAG.sub("", value)))

    @staticmethod
    def _partial_publish_error() -> UncertainPublishError:
        return UncertainPublishError(
            "Публикация отправлена частично; проверьте канал перед ручным повтором"
        )
