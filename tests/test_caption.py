from datetime import UTC, datetime

import pytest

from app.domain.exceptions import MediaValidationError
from app.domain.models import SourcePost
from app.services.caption_service import CaptionService


def test_caption_escapes_untrusted_html():
    post = SourcePost(
        provider="pixiv", source_id="1", source_url="https://x", normalized_url="https://x?a=1&b=2",
        title="<script>x</script>", author_name="A & B", author_url='https://x/?q="bad"', media_items=[],
    )
    caption = CaptionService().build(post, ["art"])
    assert "<script>" not in caption
    assert "&lt;script&gt;" in caption
    assert "A &amp; B" in caption
    assert "#art" in caption


def test_caption_includes_clean_description_and_source_date():
    post = SourcePost(
        provider="pixiv", source_id="1", source_url="https://x", normalized_url="https://x",
        title="Title", description="<p>Short &amp; <b>useful</b><br>description.</p>",
        author_name="Artist", author_url="https://x/artist", media_items=[],
        published_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
    )
    caption = CaptionService().build(post, ["art"])
    assert "Short &amp; useful description." in caption
    assert "📅 21.07.2026" in caption
    assert "🔗" in caption and "Оригинал на Pixiv" in caption


def test_caption_omits_empty_optional_fields():
    post = SourcePost(
        provider="direct", source_id="1", source_url="https://x", normalized_url="https://x",
        title="Title", author_name="Source", author_url="https://x", media_items=[],
    )
    caption = CaptionService().build(post, [])
    assert "📅" not in caption
    assert "🏷" not in caption
    assert "Открыть оригинал" in caption


def test_caption_appends_english_title_translation():
    post = SourcePost(
        provider="pixiv", source_id="1", source_url="https://x", normalized_url="https://x",
        title="どうじゃ？", author_name="Artist", author_url="https://x/artist", media_items=[],
        metadata={"title_translation": "What do you think?", "title_language": "ja"},
    )

    caption = CaptionService().build(post, [])

    assert "どうじゃ？ (TL: What do you think?)" in caption


def test_caption_is_shortened_without_cutting_html():
    post = SourcePost(
        provider="pixiv", source_id="1", source_url="https://x", normalized_url="https://x",
        title="x" * 2000, author_name="Artist", author_url="https://x/artist", media_items=[],
    )
    caption = CaptionService(limit=256).build(post, ["tag"])
    assert len(caption) <= 256
    assert caption.count("<b>") == caption.count("</b>") == 1
    assert caption.count("<a ") == caption.count("</a>") == 2


def test_caption_uses_deviantart_brand_spelling():
    post = SourcePost(
        provider="deviantart", source_id="1", source_url="https://x", normalized_url="https://x",
        title="Title", author_name="Artist", author_url="https://x/artist", media_items=[],
    )

    caption = CaptionService().build(post, [])

    assert "Оригинал на DeviantArt" in caption


def test_caption_with_oversized_required_fields_fails_instead_of_looping():
    post = SourcePost(
        provider="direct", source_id="1", source_url="https://x/image.jpg",
        normalized_url="https://x/" + "a" * 500, title="Title",
        author_name="Source", author_url="https://x", media_items=[],
    )

    with pytest.raises(MediaValidationError, match="Обязательные поля"):
        CaptionService(limit=128).build(post, [])


def test_custom_caption_is_plain_text_escaped_for_telegram_html():
    caption = CaptionService().build_custom("  <b>My & caption</b>  ")

    assert caption == "&lt;b&gt;My &amp; caption&lt;/b&gt;"


def test_custom_caption_rejects_empty_and_oversized_values():
    service = CaptionService(limit=10)

    with pytest.raises(MediaValidationError, match="не может быть пустой"):
        service.build_custom("   ")
    with pytest.raises(MediaValidationError, match="превышает лимит"):
        service.build_custom("x" * 11)
