from unittest.mock import Mock

import pytest

from app.domain.exceptions import InvalidUrlError, UnsupportedSourceError
from app.providers.direct_image import DirectImageProvider
from app.providers.pixiv import PixivProvider
from app.providers.registry import ProviderRegistry


def test_pixiv_urls_are_normalized():
    provider = PixivProvider(Mock())
    assert provider.can_handle("https://www.pixiv.net/artworks/147382169")
    assert provider.normalize_url("https://www.pixiv.net/artworks/147382169?foo=1") == "https://www.pixiv.net/en/artworks/147382169"


def test_invalid_pixiv_url():
    with pytest.raises(InvalidUrlError):
        PixivProvider(Mock()).normalize_url("https://www.pixiv.net/users/1")


def test_direct_provider_only_recognizes_images():
    provider = DirectImageProvider(Mock(), 1024)
    assert provider.can_handle("https://example.com/picture.webp")
    assert not provider.can_handle("https://example.com/page.html")


def test_registry_unsupported_source():
    with pytest.raises(UnsupportedSourceError):
        ProviderRegistry([]).resolve("https://example.com/")
