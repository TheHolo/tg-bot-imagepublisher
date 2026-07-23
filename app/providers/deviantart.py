import mimetypes
import re
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

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
    _path_re = re.compile(r"^/([^/]+)/art/([^/?#]+)-(\d+)/?$", re.IGNORECASE)
    _media_hosts = {"wixmp.com", "deviantart.net", "deviantart.com"}

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
            media_items=[MediaItem(
                url=media_url,
                preview_url=preview_url,
                filename=f"deviantart_{source_id}{extension}",
                order=0,
                mime_type=mime_type,
                width=_positive_int(payload.get("width")),
                height=_positive_int(payload.get("height")),
                headers={"Referer": normalized},
            )],
            published_at=_parse_datetime(payload.get("pubdate") or payload.get("published_time")),
            content_warning=safety if safety not in {"", "general", "safe"} else None,
            metadata={"oembed_type": payload.get("type"), "safety": safety or None},
        )


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
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _positive_int(value: object) -> int | None:
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
