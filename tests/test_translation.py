from unittest.mock import AsyncMock

from app.domain.models import SourcePost
from app.services.translation_service import TranslationService


def make_post(title: str) -> SourcePost:
    return SourcePost(
        provider="pixiv", source_id="1", source_url="https://x", normalized_url="https://x",
        title=title, author_name="Artist", author_url="https://x/artist", media_items=[],
    )


async def test_non_ascii_title_is_enriched_with_translation():
    service = TranslationService(AsyncMock())
    service.translate_to_english = AsyncMock(return_value=("What do you think?", "ja"))
    post = make_post("どうじゃ？")

    await service.enrich_title(post)

    assert post.metadata["title_translation"] == "What do you think?"
    assert post.metadata["title_language"] == "ja"


async def test_ascii_title_does_not_call_translation_api():
    service = TranslationService(AsyncMock())
    service.translate_to_english = AsyncMock()
    post = make_post("Autumn landscape")

    await service.enrich_title(post)

    service.translate_to_english.assert_not_awaited()
    assert "title_translation" not in post.metadata
