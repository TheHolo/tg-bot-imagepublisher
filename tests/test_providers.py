from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.exceptions import (
    InvalidUrlError,
    MediaTooLargeError,
    SourceAccessDeniedError,
    UnsupportedSourceError,
)
from app.providers.deviantart import DeviantArtProvider, _parse_additional_media
from app.providers.direct_image import DirectImageProvider
from app.providers.pixiv import PixivProvider, _parse_datetime
from app.providers.registry import ProviderRegistry


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status = status
        self.headers = headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[tuple, dict]] = []

    def get(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        return self.response


def test_pixiv_urls_are_normalized():
    provider = PixivProvider(Mock())
    assert provider.can_handle("https://www.pixiv.net/artworks/147382169")
    assert provider.normalize_url("https://www.pixiv.net/artworks/147382169?foo=1") == "https://www.pixiv.net/en/artworks/147382169"


def test_invalid_pixiv_url():
    with pytest.raises(InvalidUrlError):
        PixivProvider(Mock()).normalize_url("https://www.pixiv.net/users/1")


def test_pixiv_source_date_parsing():
    parsed = _parse_datetime("2026-07-21T10:30:00+09:00")
    assert parsed is not None and parsed.year == 2026 and parsed.month == 7 and parsed.day == 21
    assert _parse_datetime("not-a-date") is None


async def test_pixiv_returns_all_pages_for_selection_before_limit_check():
    provider = PixivProvider(Mock())
    pages = [
        {"urls": {"original": f"https://i.pximg.net/img-original/{index}.jpg"}, "width": 100, "height": 100}
        for index in range(11)
    ]
    provider._json = AsyncMock(side_effect=[{}, pages])

    post = await provider.fetch_post("https://www.pixiv.net/artworks/123")

    assert len(post.media_items) == 11


def test_deviantart_urls_are_recognized_and_normalized():
    provider = DeviantArtProvider(Mock())
    url = "https://deviantart.com/ExampleArtist/art/Night-City-123456789?comment=1"

    assert provider.can_handle(url)
    assert provider.normalize_url(url) == "https://www.deviantart.com/ExampleArtist/art/Night-City-123456789"
    assert not provider.can_handle("https://www.deviantart.com/ExampleArtist/gallery")


def test_invalid_deviantart_url():
    with pytest.raises(InvalidUrlError):
        DeviantArtProvider(Mock()).normalize_url("https://www.deviantart.com/ExampleArtist/gallery")


async def test_deviantart_oembed_is_mapped_to_source_post():
    provider = DeviantArtProvider(Mock())
    provider._additional_media = AsyncMock(return_value=[])
    provider._oembed = AsyncMock(return_value={
        "type": "photo",
        "title": "Night City",
        "author_name": "ExampleArtist",
        "author_url": "https://www.deviantart.com/exampleartist",
        "description": "<p>A short description.</p>",
        "fullsize_url": "https://images-wixmp.wixmp.com/image.png",
        "url": "https://images-wixmp.wixmp.com/preview.jpg",
        "thumbnail_url": "https://images-wixmp.wixmp.com/thumb.jpg",
        "imagetype": "png",
        "width": 2048,
        "height": "1536",
        "tags": "fantasy, night city, digital art",
        "pubdate": "2026-07-22T10:15:00+00:00",
        "safety": "general",
    })

    post = await provider.fetch_post(
        "https://www.deviantart.com/ExampleArtist/art/Night-City-123456789"
    )

    assert post.provider == "deviantart"
    assert post.source_id == "123456789"
    assert post.title == "Night City"
    assert post.author_name == "ExampleArtist"
    assert post.source_tags == ["fantasy", "night city", "digital art"]
    assert post.published_at is not None and post.published_at.year == 2026
    assert post.content_warning is None
    assert len(post.media_items) == 1
    assert post.author_id == "ExampleArtist"
    assert post.media_items[0].url == "https://images-wixmp.wixmp.com/image.png"
    assert post.media_items[0].preview_url == "https://images-wixmp.wixmp.com/thumb.jpg"
    assert post.media_items[0].filename == "deviantart_123456789.png"
    assert post.media_items[0].width == 2048
    assert post.media_items[0].height == 1536


async def test_deviantart_uses_available_url_when_fullsize_is_missing():
    provider = DeviantArtProvider(Mock())
    provider._additional_media = AsyncMock(return_value=[])
    provider._oembed = AsyncMock(return_value={
        "title": "Available preview",
        "author_name": "Artist",
        "url": "https://images-wixmp.wixmp.com/available.webp",
        "tags": ["one", "two"],
        "rating": "adult",
    })

    post = await provider.fetch_post(
        "https://www.deviantart.com/artist/art/Available-preview-987654321"
    )

    assert post.media_items[0].url.endswith("available.webp")
    assert post.media_items[0].filename.endswith(".webp")
    assert post.content_warning == "adult"


async def test_deviantart_rejects_post_without_accessible_image():
    provider = DeviantArtProvider(Mock())
    provider._oembed = AsyncMock(return_value={"type": "link", "title": "Private work"})

    with pytest.raises(SourceAccessDeniedError, match="нет доступного изображения"):
        await provider.fetch_post(
            "https://www.deviantart.com/artist/art/Private-work-987654321"
        )


async def test_deviantart_additional_album_images_are_mapped_in_order():
    provider = DeviantArtProvider(Mock())
    provider._oembed = AsyncMock(return_value={
        "title": "Album",
        "fullsize_url": "https://images-wixmp.wixmp.com/first.jpg",
    })
    provider._additional_media = AsyncMock(return_value=[{
        "fileId": 2,
        "media": {
            "baseUri": "https://images-wixmp.wixmp.com/f/album/",
            "prettyName": "second.png",
            "types": [
                {"t": "preview", "c": "/v1/fill/w_400,h_300/second.png", "w": 400, "h": 300},
                {"t": "fullview", "c": "/v1/fit/w_1600,h_1200/second.png", "w": 1600, "h": 1200},
            ],
            "token": ["public-token"],
        },
    }])

    post = await provider.fetch_post("https://www.deviantart.com/artist/art/Album-987654321")

    assert len(post.media_items) == 2
    assert post.metadata["page_count"] == 2
    assert post.media_items[1].order == 1
    assert "/v1/fit/" in post.media_items[1].url
    assert "token=public-token" in post.media_items[1].url
    assert "/v1/fill/" in post.media_items[1].preview_url
    assert post.media_items[1].width == 1600


def test_deviantart_embedded_additional_media_json_is_parsed():
    page = (
        r'<script>{\"additionalMedia\":'
        r'[{\"fileId\":2,\"media\":{\"baseUri\":\"https://images-wixmp.wixmp.com/f/x/\"}}],'
        r'\"next\":true}</script>'
    )

    assert _parse_additional_media(page)[0]["fileId"] == 2


def test_direct_provider_only_recognizes_images():
    provider = DirectImageProvider(Mock(), 1024)
    assert provider.can_handle("https://example.com/picture.webp")
    assert not provider.can_handle("https://example.com/page.html")


async def test_direct_provider_maps_valid_image_response(monkeypatch):
    response = FakeResponse(
        206,
        {
            "Content-Type": "image/jpeg; charset=binary",
            "Content-Length": "1",
            "Content-Range": "bytes 0-0/1200",
        },
    )
    session = FakeSession(response)
    provider = DirectImageProvider(session, 2000)
    public_dns = AsyncMock()
    monkeypatch.setattr("app.providers.direct_image.ensure_public_dns", public_dns)

    post = await provider.fetch_post("https://example.com/My%20Image.jpg")

    assert post.title == "My Image"
    assert post.media_items[0].filename == "My Image.jpg"
    assert post.media_items[0].size == 1200
    assert session.requests[0][1]["allow_redirects"] is False
    public_dns.assert_awaited_once_with("https://example.com/My%20Image.jpg")


async def test_direct_provider_uses_range_total_for_size_limit(monkeypatch):
    response = FakeResponse(
        206,
        {
            "Content-Type": "image/png",
            "Content-Length": "1",
            "Content-Range": "bytes 0-0/5000",
        },
    )
    provider = DirectImageProvider(FakeSession(response), 1000)
    monkeypatch.setattr("app.providers.direct_image.ensure_public_dns", AsyncMock())

    with pytest.raises(MediaTooLargeError):
        await provider.fetch_post("https://example.com/image.png")


def test_registry_unsupported_source():
    with pytest.raises(UnsupportedSourceError):
        ProviderRegistry([]).resolve("https://example.com/")
