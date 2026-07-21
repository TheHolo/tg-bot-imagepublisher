import asyncio
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.domain.exceptions import MediaValidationError
from app.domain.models import DownloadedMedia, PreparedMedia


class MediaService:
    async def prepare(self, media: DownloadedMedia, mode: str) -> PreparedMedia:
        try:
            width, height, fmt = await asyncio.to_thread(self._verify, media.path)
        except (OSError, UnidentifiedImageError) as error:
            raise MediaValidationError("Изображение повреждено или имеет неизвестный формат") from error
        media.width, media.height = width, height
        photo_ok = fmt in {"JPEG", "PNG", "WEBP"} and media.size <= 10 * 1024 * 1024 and width + height <= 10000
        return PreparedMedia(media.path, as_document=(mode == "document" or (mode == "auto" and not photo_ok)), order=media.source.order)

    @staticmethod
    def _verify(path: Path) -> tuple[int, int, str | None]:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.width, image.height, image.format
