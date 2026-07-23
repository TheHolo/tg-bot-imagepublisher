from unittest.mock import AsyncMock

import pytest

from app.domain.exceptions import MediaTooLargeError
from app.domain.models import MediaItem
from app.services import download_service
from app.services.download_service import DownloadService


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, *, headers: dict[str, str], chunks: list[bytes], status: int = 200) -> None:
        self.headers = headers
        self.status = status
        self.content = FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def get(self, *args, **kwargs) -> FakeResponse:
        return self.response


def media_item() -> MediaItem:
    return MediaItem(url="https://example.com/image.png", filename="image.png", order=0)


async def test_download_rejects_declared_size_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(download_service, "ensure_public_dns", AsyncMock())
    response = FakeResponse(
        headers={"Content-Type": "image/png", "Content-Length": "101"},
        chunks=[b"not-written"],
    )
    service = DownloadService(FakeSession(response), tmp_path, max_size=100)

    with pytest.raises(MediaTooLargeError):
        await service.download(1, media_item())

    assert list(tmp_path.rglob("*.part")) == []
    assert list(tmp_path.rglob("*.png")) == []


async def test_download_rejects_stream_that_exceeds_limit_and_removes_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(download_service, "ensure_public_dns", AsyncMock())
    response = FakeResponse(
        headers={"Content-Type": "image/png"},
        chunks=[b"a" * 60, b"b" * 41],
    )
    service = DownloadService(FakeSession(response), tmp_path, max_size=100)

    with pytest.raises(MediaTooLargeError):
        await service.download(1, media_item())

    assert list(tmp_path.rglob("*.part")) == []
    assert list(tmp_path.rglob("*.png")) == []


async def test_download_at_limit_is_saved_atomically(tmp_path, monkeypatch):
    public_dns = AsyncMock()
    monkeypatch.setattr(download_service, "ensure_public_dns", public_dns)
    response = FakeResponse(
        headers={"Content-Type": "image/png", "Content-Length": "100"},
        chunks=[b"a" * 60, b"b" * 40],
    )
    service = DownloadService(FakeSession(response), tmp_path, max_size=100)

    downloaded = await service.download(7, media_item())

    assert downloaded.size == 100
    assert downloaded.path.read_bytes() == b"a" * 60 + b"b" * 40
    assert list(tmp_path.rglob("*.part")) == []
    public_dns.assert_awaited_once_with(media_item().url)
