import asyncio
from html import unescape
import logging

import aiohttp

from app.domain.models import SourcePost

logger = logging.getLogger(__name__)
MYMEMORY_URL = "https://api.mymemory.translated.net/get"


class TranslationService:
    def __init__(self, session: aiohttp.ClientSession, enabled: bool = True, timeout: int = 5) -> None:
        self.session = session
        self.enabled = enabled
        self.timeout = timeout
        self.cache: dict[str, tuple[str, str] | None] = {}

    async def enrich_title(self, post: SourcePost) -> None:
        if not self.enabled or not post.title or post.metadata.get("title_translation"):
            return
        if not any(character.isalpha() and not character.isascii() for character in post.title):
            return
        result = await self.translate_to_english(post.title)
        if not result:
            return
        translated, detected_language = result
        if detected_language.lower().startswith("en") or translated.casefold() == post.title.casefold():
            return
        post.metadata["title_translation"] = translated
        post.metadata["title_language"] = detected_language

    async def translate_to_english(self, text: str) -> tuple[str, str] | None:
        if text in self.cache:
            return self.cache[text]
        try:
            async with asyncio.timeout(self.timeout):
                async with self.session.get(
                    MYMEMORY_URL, params={"q": text, "langpair": "autodetect|en"}
                ) as response:
                    if response.status != 200:
                        logger.warning("title_translation_http_error status=%s", response.status)
                        self.cache[text] = None
                        return None
                    data = await response.json(content_type=None)
            response_data = data.get("responseData") or {}
            translated = unescape(str(response_data.get("translatedText") or "")).strip()
            detected = str(response_data.get("detectedLanguage") or "").strip()
            result = (translated, detected) if translated and detected else None
        except (TimeoutError, aiohttp.ClientError, ValueError, TypeError):
            logger.warning("title_translation_failed", exc_info=True)
            result = None
        self.cache[text] = result
        return result
