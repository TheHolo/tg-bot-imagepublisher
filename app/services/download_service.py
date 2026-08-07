import os
import re
from pathlib import Path

import aiohttp

from app.domain.exceptions import (
    ApplicationError,
    DownloadError,
    MediaTooLargeError,
    MediaValidationError,
)
from app.domain.models import DownloadedMedia, MediaItem
from app.utils.urls import ensure_public_dns

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class DownloadService:
    def __init__(self, session: aiohttp.ClientSession, storage: Path, max_size: int) -> None:
        self.session = session
        self.storage = storage.resolve()
        self.max_size = max_size

    async def download(self, job_id: int | str, item: MediaItem) -> DownloadedMedia:
        directory = (self.storage / "jobs" / str(job_id)).resolve()
        if self.storage not in directory.parents:
            raise DownloadError("Небезопасный путь хранения")
        directory.mkdir(parents=True, exist_ok=True)
        name = _SAFE.sub("_", Path(item.filename).name) or f"{item.order:03d}.bin"
        target = directory / f"{item.order + 1:03d}_{name}"
        temporary = target.with_suffix(target.suffix + ".part")
        size = 0
        try:
            await ensure_public_dns(item.url)
            async with self.session.get(item.url, headers=item.headers, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    raise DownloadError("Небезопасное перенаправление при загрузке")
                if response.status >= 400:
                    raise DownloadError(f"Ошибка загрузки HTTP {response.status}")
                mime = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0]
                if not mime.startswith("image/"):
                    raise MediaValidationError("Полученный файл не является изображением")
                declared = _content_length(response.headers.get("Content-Length"))
                if declared > self.max_size:
                    raise MediaTooLargeError("Файл превышает лимит")
                with temporary.open("wb") as stream:
                    async for chunk in response.content.iter_chunked(128 * 1024):
                        size += len(chunk)
                        if size > self.max_size:
                            raise MediaTooLargeError("Файл превышает лимит")
                        stream.write(chunk)
            os.replace(temporary, target)
            return DownloadedMedia(item, target, mime, size)
        except ApplicationError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as error:
            raise DownloadError("Не удалось загрузить файл") from error
        finally:
            temporary.unlink(missing_ok=True)


def _content_length(value: str | None) -> int:
    try:
        return max(0, int(value or 0))
    except ValueError:
        return 0
