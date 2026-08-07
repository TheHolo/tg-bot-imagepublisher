from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.domain.models import DownloadedMedia, MediaItem, PreparedMedia, SourcePost
from app.services.job_service import serialize_post
from app.services.preview_service import PreviewService, deserialize_post


def test_post_serialization_preserves_content_warning():
    post = SourcePost(
        provider="deviantart",
        source_id="1",
        source_url="https://example.com/post",
        normalized_url="https://example.com/post",
        title="Title",
        author_name="Author",
        author_url="https://example.com/author",
        media_items=[],
        content_warning="adult",
    )

    restored = deserialize_post(serialize_post(post))

    assert restored.content_warning == "adult"


async def test_preview_downloads_lightweight_url_instead_of_original(tmp_path):
    downloader = SimpleNamespace(download=AsyncMock())
    media = SimpleNamespace(prepare=AsyncMock())
    publisher = SimpleNamespace(preview=AsyncMock())
    captions = SimpleNamespace(build=Mock(return_value="caption"))
    translator = SimpleNamespace(enrich_title=AsyncMock())
    source = MediaItem(
        url="https://example.com/original.png",
        preview_url="https://example.com/preview.jpg",
        filename="image.png",
        order=0,
    )
    post = SourcePost(
        provider="pixiv",
        source_id="1",
        source_url="https://example.com/post",
        normalized_url="https://example.com/post",
        title="Title",
        author_name="Author",
        author_url="https://example.com/author",
        media_items=[source],
    )
    downloaded = DownloadedMedia(source, tmp_path / "preview.jpg", "image/jpeg", 10)
    prepared = PreparedMedia(Path(downloaded.path), as_document=False, order=0)
    downloader.download.return_value = downloaded
    media.prepare.return_value = prepared
    service = PreviewService(
        downloader=downloader,
        media=media,
        captions=captions,
        publisher=publisher,
        storage=tmp_path,
        auto_add_source_tags=False,
        max_tags=20,
        max_tag_length=64,
        translator=translator,
    )
    job = SimpleNamespace(
        id=7,
        post_data=serialize_post(post),
        channel=SimpleNamespace(publish_mode="auto", caption_template=None),
        caption_override=None,
        user_tags=[],
        source_tags=[],
    )

    await service.send(job, 123)

    preview_item = downloader.download.await_args.args[1]
    assert preview_item.url == source.preview_url
    assert preview_item.preview_url == source.preview_url
    publisher.preview.assert_awaited_once_with(123, [prepared], "caption")
