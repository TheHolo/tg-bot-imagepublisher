import pytest

from app.domain.exceptions import InvalidMediaSelectionError, InvalidUrlError
from app.utils.input import (
    parse_media_selection,
    parse_submission,
    parse_submission_batch,
    split_source_selection,
)
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


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a.jpg",
        "http://[::1]/a.png",
        "ftp://example.com/a.jpg",
        "https://user:secret@example.com/a.jpg",
        "https://:secret@example.com/a.jpg",
    ],
)
def test_private_or_invalid_urls_rejected(url):
    with pytest.raises(InvalidUrlError):
        validate_public_url(url)


def test_url_punctuation_is_trimmed():
    assert extract_urls("see (https://example.com/image.jpg).") == ["https://example.com/image.jpg"]
    assert parse_submission_batch("see (https://example.com/image.jpg).") == (
        ["https://example.com/image.jpg"], ["see"], None,
    )


def test_image_selection_is_attached_to_preceding_url_and_not_added_to_tags():
    urls, tags, channel = parse_submission_batch(
        "https://www.pixiv.net/en/artworks/140228555 [1, 3, 5-7, 10] art --channel works"
    )

    assert urls == ["https://www.pixiv.net/en/artworks/140228555 [1,3,5,6,7,10]"]
    assert tags == ["art"]
    assert channel == "works"
    assert split_source_selection(urls[0]) == (
        "https://www.pixiv.net/en/artworks/140228555",
        (1, 3, 5, 6, 7, 10),
    )


def test_each_url_can_have_its_own_image_selection():
    urls, tags, _ = parse_submission_batch(
        "https://www.pixiv.net/artworks/1 [2] "
        "https://www.deviantart.com/artist/art/work-2 [3-4] tag"
    )

    assert urls == [
        "https://www.pixiv.net/artworks/1 [2]",
        "https://www.deviantart.com/artist/art/work-2 [3,4]",
    ]
    assert tags == ["tag"]


def test_media_selection_deduplicates_numbers_and_preserves_requested_order():
    assert parse_media_selection("5,1-3,2,5") == (5, 1, 2, 3)


@pytest.mark.parametrize("selection", ["", "0", "3-1", "one", "1,,2"])
def test_invalid_media_selection_is_rejected(selection):
    with pytest.raises(InvalidMediaSelectionError):
        parse_submission_batch(f"https://www.pixiv.net/artworks/1 [{selection}]")


def test_unclosed_media_selection_is_rejected():
    with pytest.raises(InvalidMediaSelectionError, match="Не закрыта"):
        parse_submission_batch("https://www.pixiv.net/artworks/1 [1-3")


def test_unclosed_tag_quote_is_reported_as_invalid_input():
    with pytest.raises(InvalidUrlError, match="кавычк"):
        parse_submission_batch('https://example.com/image.jpg "unfinished')
