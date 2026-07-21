from PIL import Image

from app.domain.models import DownloadedMedia, MediaItem
from app.services import media_service
from app.services.media_service import MediaService


async def test_oversized_supported_image_is_converted_to_telegram_photo(tmp_path, monkeypatch):
    source_path = tmp_path / "large.png"
    Image.effect_noise((300, 300), 100).convert("RGB").save(source_path, "PNG")
    source = MediaItem(url="https://example.com/large.png", filename="large.png", order=0)
    downloaded = DownloadedMedia(source, source_path, "image/png", source_path.stat().st_size)
    monkeypatch.setattr(media_service, "PHOTO_MAX_BYTES", 1_000)
    monkeypatch.setattr(media_service, "PHOTO_TARGET_BYTES", 8_000)

    prepared = await MediaService().prepare(downloaded, "auto")

    assert prepared.as_document is False
    assert prepared.path != source_path
    assert prepared.path.suffix == ".jpg"
    assert prepared.path.stat().st_size <= 8_000


async def test_document_mode_keeps_original_file(tmp_path):
    source_path = tmp_path / "original.png"
    Image.new("RGB", (100, 100), "red").save(source_path, "PNG")
    source = MediaItem(url="https://example.com/original.png", filename="original.png", order=0)
    downloaded = DownloadedMedia(source, source_path, "image/png", source_path.stat().st_size)

    prepared = await MediaService().prepare(downloaded, "document")

    assert prepared.as_document is True
    assert prepared.path == source_path
