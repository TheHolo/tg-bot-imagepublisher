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


def test_caption_is_shortened_without_cutting_html():
    post = SourcePost(
        provider="pixiv", source_id="1", source_url="https://x", normalized_url="https://x",
        title="x" * 2000, author_name="Artist", author_url="https://x/artist", media_items=[],
    )
    caption = CaptionService(limit=256).build(post, ["tag"])
    assert len(caption) <= 256
    assert caption.count("<b>") == caption.count("</b>") == 1
    assert caption.count("<a ") == caption.count("</a>") == 2
