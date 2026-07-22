from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.exceptions import InvalidUrlError, SourceAccessDeniedError, TooManyMediaError, UnsupportedSourceError
from app.providers.deviantart import DeviantArtProvider
from app.providers.direct_image import DirectImageProvider
from app.providers.pixiv import PixivProvider, _parse_datetime
from app.providers.registry import ProviderRegistry


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


async def test_pixiv_rejects_work_with_more_than_configured_image_limit():
    provider = PixivProvider(Mock(), media_limit_enabled=True, max_images=10)
    provider._json = AsyncMock(side_effect=[{}, [{} for _ in range(11)]])

    with pytest.raises(TooManyMediaError, match="11 изображений.*10"):
        await provider.fetch_post("https://www.pixiv.net/artworks/123")


async def test_pixiv_image_limit_can_be_disabled():
    provider = PixivProvider(Mock(), media_limit_enabled=False, max_images=10)
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


def test_direct_provider_only_recognizes_images():
    provider = DirectImageProvider(Mock(), 1024)
    assert provider.can_handle("https://example.com/picture.webp")
    assert not provider.can_handle("https://example.com/page.html")


def test_registry_unsupported_source():
    with pytest.raises(UnsupportedSourceError):
        ProviderRegistry([]).resolve("https://example.com/")
