import hashlib
import mimetypes
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from app.domain.exceptions import InvalidUrlError, MediaTooLargeError, SourceNotFoundError
from app.domain.models import MediaItem, SourcePost
from app.providers.base import BaseProvider
from app.utils.urls import ensure_public_dns, validate_public_url


class DirectImageProvider(BaseProvider):
    name = "direct"
    extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    def __init__(self, session, max_size: int) -> None:
        super().__init__(session)
        self.max_size = max_size

    def can_handle(self, url: str) -> bool:
        try:
            validate_public_url(url)
        except InvalidUrlError:
            return False
        return PurePosixPath(urlsplit(url).path).suffix.lower() in self.extensions

    def normalize_url(self, url: str) -> str:
        return validate_public_url(url)

    async def fetch_post(self, url: str) -> SourcePost:
        normalized = self.normalize_url(url)
        await ensure_public_dns(normalized)
        async with self.session.get(normalized, headers={"Range": "bytes=0-0"}, allow_redirects=False) as response:
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise InvalidUrlError("Перенаправление без адреса")
                # Redirect targets are deliberately rejected here to prevent SSRF.
                raise InvalidUrlError("Прямые ссылки с перенаправлением не поддерживаются")
            if response.status == 404:
                raise SourceNotFoundError("Изображение не найдено")
            if response.status not in {200, 206}:
                raise InvalidUrlError(f"Сервер изображения ответил HTTP {response.status}")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            size = int(response.headers.get("Content-Length", 0))
        if not content_type.startswith("image/"):
            raise InvalidUrlError("Ссылка ведёт не на изображение")
        if size > self.max_size:
            raise MediaTooLargeError("Изображение превышает допустимый размер")
        parsed = urlsplit(normalized)
        filename = unquote(PurePosixPath(parsed.path).name) or "image"
        if not PurePosixPath(filename).suffix:
            filename += mimetypes.guess_extension(content_type) or ".img"
        source_id = hashlib.sha256(normalized.encode()).hexdigest()
        return SourcePost(
            provider=self.name,
            source_id=source_id,
            source_url=url,
            normalized_url=normalized,
            title=PurePosixPath(filename).stem,
            author_name=parsed.hostname or "Источник",
            author_url=f"{parsed.scheme}://{parsed.hostname}",
            media_items=[MediaItem(url=normalized, filename=filename, order=0, mime_type=content_type, size=size)],
        )
