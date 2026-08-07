from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from app.news.classifier import normalize_telegram_post_url, telegram_post_parts
from app.news.errors import (
    EmptyNewsContentError,
    NewsSourceAccessError,
    UnsafeNewsUrlError,
)
from app.news.http import SafeHttpFetcher
from app.news.models import (
    ExtractedNewsSource,
    ExtractionProgress,
    ExtractionStage,
    NewsMedia,
    NewsMediaKind,
    NewsSourceKind,
    ProgressCallback,
)

_TELEGRAM_WEB_HOSTS = frozenset({"t.me", "telegram.me"})
_BACKGROUND_URL_RE = re.compile(
    r"background-image\s*:\s*url\(['\"]?([^)'\"]+)", re.IGNORECASE
)


@dataclass(slots=True)
class ForwardedTelegramPost:
    message_id: int
    text: str
    channel_id: str | None = None
    channel_username: str | None = None
    channel_title: str | None = None
    author_name: str | None = None
    published_at: datetime | None = None
    media: list[NewsMedia] = field(default_factory=list)
    has_protected_content: bool = False


class TelegramPostExtractor:
    """Extract one explicitly submitted public post; no history or userbot access."""

    def __init__(self, fetcher: SafeHttpFetcher) -> None:
        self._fetcher = fetcher

    async def extract(
        self,
        url: str,
        *,
        progress: ProgressCallback | None = None,
    ) -> ExtractedNewsSource:
        normalized_url = normalize_telegram_post_url(url)
        username, message_id = telegram_post_parts(normalized_url)
        await _emit(progress, "Получаем Telegram-пост")
        embed_url = f"{normalized_url}?embed=1&mode=tme"
        page = await self._fetcher.fetch_text(
            embed_url,
            allowed_hosts=_TELEGRAM_WEB_HOSTS,
        )
        parser = _TelegramEmbedParser()
        parser.feed(page.text)
        parser.close()

        text = _clean_block("".join(parser.text_parts))
        page_text = _clean_block("\n".join(parser.all_text))
        if parser.has_error or (not text and _looks_unavailable(page_text)):
            raise NewsSourceAccessError(
                "Telegram-пост недоступен: канал закрыт, пост удалён или запрещено встраивание"
            )
        if not text:
            text = _clean_block(parser.og_description)
        if not text:
            raise EmptyNewsContentError(
                "В Telegram-посте нет доступного текста для обработки"
            )

        media: list[NewsMedia] = []
        for kind, candidate in parser.media:
            media_url = urljoin(page.final_url, html.unescape(candidate))
            try:
                await self._fetcher.ensure_safe_url(media_url)
            except UnsafeNewsUrlError:
                continue
            if all(item.url != media_url for item in media):
                media.append(NewsMedia(kind=kind, url=media_url))

        author_name = _clean_inline(" ".join(parser.author_parts)) or username
        return ExtractedNewsSource(
            kind=NewsSourceKind.TELEGRAM,
            source_id=f"{username}:{message_id}",
            source_url=url,
            normalized_url=normalized_url,
            title=_title(text, author_name),
            raw_text=text,
            author_name=author_name,
            author_url=f"https://t.me/{username}",
            published_at=_parse_datetime(parser.published_at),
            media=media,
            metadata={
                "extractor": "telegram-public-embed",
                "channel_username": username,
                "message_id": message_id,
                "scope": "single-user-submitted-public-post",
            },
        )

    async def extract_forwarded(
        self,
        post: ForwardedTelegramPost,
        *,
        progress: ProgressCallback | None = None,
    ) -> ExtractedNewsSource:
        await _emit(progress, "Получаем пересланный Telegram-пост")
        if post.has_protected_content:
            raise NewsSourceAccessError(
                "Автор запретил пересылку содержимого этого поста"
            )
        text = _clean_block(post.text)
        if not text:
            raise EmptyNewsContentError("В пересланном посте нет текста для обработки")
        username = (post.channel_username or "").lstrip("@") or None
        source_url = f"https://t.me/{username}/{post.message_id}" if username else None
        source_id = f"{post.channel_id or username or 'forward'}:{post.message_id}"
        author_name = post.author_name or post.channel_title or username
        return ExtractedNewsSource(
            kind=NewsSourceKind.TELEGRAM,
            source_id=source_id,
            source_url=source_url,
            normalized_url=source_url,
            title=_title(text, author_name),
            raw_text=text,
            author_name=author_name,
            author_url=f"https://t.me/{username}" if username else None,
            published_at=post.published_at,
            media=list(post.media),
            metadata={
                "extractor": "telegram-bot-forward",
                "channel_id": post.channel_id,
                "channel_username": username,
                "message_id": post.message_id,
                "scope": "single-user-forwarded-post",
            },
        )


class _TelegramEmbedParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.author_parts: list[str] = []
        self.all_text: list[str] = []
        self.media: list[tuple[NewsMediaKind, str]] = []
        self.published_at: str | None = None
        self.og_description = ""
        self.has_error = False
        self._text_stack: list[bool] = []
        self._author_stack: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        in_text = (self._text_stack[-1] if self._text_stack else False) or bool(
            classes & {"tgme_widget_message_text", "js-message_text"}
        )
        in_author = (self._author_stack[-1] if self._author_stack else False) or bool(
            classes & {"tgme_widget_message_owner_name", "tgme_widget_message_author"}
        )
        is_void = tag in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "source",
            "track",
            "wbr",
        }
        if not is_void:
            self._text_stack.append(in_text)
            self._author_stack.append(in_author)
        if classes & {"tgme_widget_message_error", "tgme_widget_message_error_icon"}:
            self.has_error = True
        if tag == "br" and in_text:
            self.text_parts.append("\n")
        if tag == "time" and attributes.get("datetime"):
            self.published_at = attributes["datetime"]
        if tag == "meta" and attributes.get("property") == "og:description":
            self.og_description = attributes.get("content") or ""
        style = attributes.get("style") or ""
        match = _BACKGROUND_URL_RE.search(style)
        if match and classes & {
            "tgme_widget_message_photo_wrap",
            "tgme_widget_message_link_preview_image",
        }:
            self.media.append((NewsMediaKind.IMAGE, match.group(1)))
        if tag in {"video", "source"} and attributes.get("src"):
            self.media.append((NewsMediaKind.VIDEO, attributes["src"] or ""))
        if (
            tag == "img"
            and attributes.get("src")
            and classes
            & {
                "tgme_widget_message_video_thumb",
                "tgme_widget_message_photo",
            }
        ):
            self.media.append((NewsMediaKind.IMAGE, attributes["src"] or ""))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._text_stack:
            self._text_stack.pop()
        if self._author_stack:
            self._author_stack.pop()

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.all_text.append(data)
            if self._text_stack and self._text_stack[-1]:
                self.text_parts.append(data)
            if self._author_stack and self._author_stack[-1]:
                self.author_parts.append(data)


async def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        await progress(ExtractionProgress(ExtractionStage.EXTRACTING_CONTENT, message))


def _clean_inline(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).split())


def _clean_block(value: Any) -> str:
    lines = [_clean_inline(line) for line in str(value or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _title(text: str, author_name: str | None) -> str:
    first_line = _clean_inline(text.splitlines()[0])[:180]
    if len(first_line) >= 20:
        return first_line
    prefix = f"{author_name}: " if author_name else ""
    return f"{prefix}{first_line or 'Telegram-пост'}"[:180]


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _looks_unavailable(value: str) -> bool:
    lowered = value.casefold()
    return any(
        marker in lowered
        for marker in (
            "post not found",
            "message not found",
            "channel private",
            "публикация не найдена",
            "сообщение не найдено",
        )
    )
