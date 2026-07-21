from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.exceptions import InvalidUrlError, TooManyMediaError, UnsupportedSourceError
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


def test_direct_provider_only_recognizes_images():
    provider = DirectImageProvider(Mock(), 1024)
    assert provider.can_handle("https://example.com/picture.webp")
    assert not provider.can_handle("https://example.com/page.html")


def test_registry_unsupported_source():
    with pytest.raises(UnsupportedSourceError):
        ProviderRegistry([]).resolve("https://example.com/")
