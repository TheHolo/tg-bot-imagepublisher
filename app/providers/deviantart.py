import html
import json
import mimetypes
import re
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import aiohttp

from app.domain.exceptions import (
    InvalidUrlError,
    SourceAccessDeniedError,
    SourceNotFoundError,
    SourceRateLimitedError,
)
from app.domain.models import MediaItem, SourcePost
from app.providers.base import BaseProvider
from app.utils.urls import validate_public_url


class DeviantArtProvider(BaseProvider):
    name = "deviantart"
    _oembed_url = "https://backend.deviantart.com/oembed"
    healthcheck_url = _oembed_url
    healthcheck_statuses = frozenset({200, 400, 404})
    _path_re = re.compile(r"^/([^/]+)/art/([^/?#]+)-(\d+)/?$", re.IGNORECASE)
    _media_hosts = frozenset({"wixmp.com", "deviantart.net", "deviantart.com"})

    def can_handle(self, url: str) -> bool:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return host in {"deviantart.com", "www.deviantart.com"} and bool(self._path_re.match(parsed.path))

    def _parts(self, url: str) -> tuple[str, str, str]:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        match = self._path_re.match(parsed.path)
        if parsed.scheme.lower() not in {"http", "https"} or host not in {"deviantart.com", "www.deviantart.com"} or not match:
            raise InvalidUrlError("Некорректная ссылка DeviantArt")
        return match.group(1), match.group(2), match.group(3)

    def normalize_url(self, url: str) -> str:
        author, slug, source_id = self._parts(url)
        return f"https://www.deviantart.com/{author}/art/{slug}-{source_id}"

    async def _oembed(self, normalized: str) -> dict:
        async with self.session.get(
            self._oembed_url,
            params={"url": normalized, "format": "json"},
            headers={"Accept": "application/json", "Referer": normalized},
        ) as response:
            if response.status == 404:
                raise SourceNotFoundError("Работа DeviantArt удалена или не найдена")
            if response.status in {401, 403}:
                raise SourceAccessDeniedError("DeviantArt не предоставил доступ к публикации")
            if response.status == 429:
                raise SourceRateLimitedError("DeviantArt временно ограничил запросы")
            response.raise_for_status()
            payload = await response.json(content_type=None)
        if not isinstance(payload, dict):
            raise SourceNotFoundError("DeviantArt вернул некорректные данные публикации")
        return payload

    async def _additional_media(self, normalized: str) -> list[dict]:
        """Read Eclipse's public album metadata embedded in a deviation page."""
        try:
            async with self.session.get(
                normalized,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.deviantart.com/",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/138.0.0.0 Safari/537.36"
                    ),
                },
            ) as response:
                if response.status != 200:
                    return []
                page = await response.text()
        except (aiohttp.ClientError, TimeoutError):
            return []
        return _parse_additional_media(page)

    async def fetch_post(self, url: str) -> SourcePost:
        url_author, _, source_id = self._parts(url)
        normalized = self.normalize_url(url)
        payload = await self._oembed(normalized)
        media_url = str(payload.get("fullsize_url") or payload.get("url") or "").strip()
        if not media_url:
            raise SourceAccessDeniedError("В публикации DeviantArt нет доступного изображения")
        media_url = validate_public_url(_absolute_url(media_url), self._media_hosts)
        author_name = str(payload.get("author_name") or "Неизвестный автор")
        author_url = str(payload.get("author_url") or f"https://www.deviantart.com/{url_author}")
        try:
            author_url = validate_public_url(_absolute_url(author_url), {"deviantart.com"})
        except InvalidUrlError:
            author_url = f"https://www.deviantart.com/{url_author}"
        preview_url = str(payload.get("thumbnail_url") or "").strip() or None
        if preview_url:
            preview_url = validate_public_url(_absolute_url(preview_url), self._media_hosts)
        mime_type = _mime_type(media_url, payload.get("imagetype"))
        extension = mimetypes.guess_extension(mime_type or "") or _extension(media_url)
        safety = str(payload.get("safety") or payload.get("rating") or "").strip().lower()
        media_items = [MediaItem(
            url=media_url,
            preview_url=preview_url,
            filename=f"deviantart_{source_id}{extension}",
            order=0,
            mime_type=mime_type,
            width=_positive_int(payload.get("width")),
            height=_positive_int(payload.get("height")),
            headers={"Referer": normalized},
        )]
        for item in await self._additional_media(normalized):
            media = _additional_media_item(item, source_id, len(media_items), normalized)
            if media is not None and all(existing.url != media.url for existing in media_items):
                media_items.append(media)
        return SourcePost(
            provider=self.name,
            source_id=source_id,
            source_url=url,
            normalized_url=normalized,
            title=str(payload.get("title") or "Без названия"),
            description=str(payload.get("description") or ""),
            author_id=url_author,
            author_name=author_name,
            author_url=author_url,
            source_tags=_tags(payload.get("tags")),
            media_items=media_items,
            published_at=_parse_datetime(payload.get("pubdate") or payload.get("published_time")),
            content_warning=safety if safety not in {"", "general", "safe"} else None,
            metadata={
                "oembed_type": payload.get("type"),
                "safety": safety or None,
                "page_count": len(media_items),
            },
        )


def _parse_additional_media(page: str) -> list[dict]:
    # Eclipse serializes its page state both as ordinary JSON and as JSON with
    # escaped quotes inside another string. Normalizing both forms keeps this
    # parser independent from surrounding script markup.
    normalized = html.unescape(page).replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    for marker in re.finditer(r'"additionalMedia"\s*:\s*', normalized):
        try:
            value, _ = json.JSONDecoder().raw_decode(normalized, marker.end())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _additional_media_item(
    item: dict, source_id: str, order: int, referer: str,
) -> MediaItem | None:
    media = item.get("media")
    if not isinstance(media, dict):
        return None
    media_url, fullview = _eclipse_media_url(media, "fullview")
    if not media_url:
        return None
    try:
        media_url = validate_public_url(_absolute_url(media_url), DeviantArtProvider._media_hosts)
    except InvalidUrlError:
        return None
    preview_url, _ = _eclipse_media_url(media, "preview")
    try:
        preview_url = (
            validate_public_url(_absolute_url(preview_url), DeviantArtProvider._media_hosts)
            if preview_url else media_url
        )
    except InvalidUrlError:
        preview_url = media_url
    mime_type = _mime_type(media_url, None)
    extension = mimetypes.guess_extension(mime_type or "") or _extension(media_url)
    return MediaItem(
        url=media_url,
        preview_url=preview_url,
        filename=f"deviantart_{source_id}_p{order}{extension}",
        order=order,
        mime_type=mime_type,
        width=_positive_int(fullview.get("w")),
        height=_positive_int(fullview.get("h")),
        headers={"Referer": referer},
    )


def _eclipse_media_url(media: dict, format_name: str) -> tuple[str, dict]:
    base_uri = str(media.get("baseUri") or "").strip()
    formats = {
        str(item.get("t")): item
        for item in media.get("types") or []
        if isinstance(item, dict)
    }
    selected_format = formats.get(format_name) or {}
    tokens = media.get("token") or []
    if isinstance(tokens, str):
        tokens = [tokens]
    if base_uri and tokens:
        if len(tokens) <= 1 and selected_format.get("c"):
            base_uri += str(selected_format["c"]).replace(
                "<prettyName>", str(media.get("prettyName") or "image")
            )
        separator = "&" if "?" in base_uri else "?"
        base_uri += f"{separator}token={tokens[-1]}"
    return base_uri, selected_format


def _tags(value: object) -> list[str]:
    if isinstance(value, str):
        return [tag.strip() for tag in value.split(",") if tag.strip()]
    if isinstance(value, list):
        return [str(tag).strip() for tag in value if str(tag).strip()]
    return []


def _absolute_url(value: str) -> str:
    return f"https:{value}" if value.startswith("//") else value


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _positive_int(value: object) -> int | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _mime_type(url: str, value: object) -> str | None:
    candidate = str(value or "").strip().lower()
    if candidate.startswith("image/"):
        return candidate
    if candidate:
        candidate = candidate.removeprefix(".")
        guessed = mimetypes.types_map.get(f".{candidate}")
        if guessed and guessed.startswith("image/"):
            return guessed
    guessed, _ = mimetypes.guess_type(unquote(urlsplit(url).path))
    return guessed if guessed and guessed.startswith("image/") else None


def _extension(url: str) -> str:
    suffix = PurePosixPath(unquote(urlsplit(url).path)).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
