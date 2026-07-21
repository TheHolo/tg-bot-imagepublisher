import pytest

from app.domain.exceptions import InvalidUrlError
from app.utils.input import parse_submission, parse_submission_batch
from app.utils.urls import extract_urls, validate_public_url


def test_parse_submission_any_url_position():
    assert parse_submission("art https://example.com/a.jpg #night --channel works") == (
        "https://example.com/a.jpg", ["art", "#night"], "works", 0
    )


def test_multiple_urls_are_counted_and_ignored_as_tags():
    result = parse_submission("https://a.test/a.jpg tag https://b.test/b.png")
    assert result[0] == "https://a.test/a.jpg" and result[3] == 1 and result[1] == ["tag"]


def test_batch_urls_support_commas_with_or_without_spaces():
    urls, tags, channel = parse_submission_batch(
        "https://a.test/a.jpg,https://b.test/b.png, https://c.test/c.webp art --channel works"
    )
    assert urls == ["https://a.test/a.jpg", "https://b.test/b.png", "https://c.test/c.webp"]
    assert tags == ["art"]
    assert channel == "works"


def test_batch_urls_are_deduplicated_and_limited():
    urls, _, _ = parse_submission_batch("https://a.test/a.jpg https://a.test/a.jpg")
    assert urls == ["https://a.test/a.jpg"]
    with pytest.raises(InvalidUrlError):
        parse_submission_batch("https://a.test/a.jpg https://b.test/b.jpg", max_urls=1)


def test_no_url():
    with pytest.raises(InvalidUrlError):
        parse_submission("only tags")


@pytest.mark.parametrize("url", ["http://127.0.0.1/a.jpg", "http://[::1]/a.png", "ftp://example.com/a.jpg"])
def test_private_or_invalid_urls_rejected(url):
    with pytest.raises(InvalidUrlError):
        validate_public_url(url)


def test_url_punctuation_is_trimmed():
    assert extract_urls("see (https://example.com/image.jpg).") == ["https://example.com/image.jpg"]
