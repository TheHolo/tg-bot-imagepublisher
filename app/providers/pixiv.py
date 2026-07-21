import re
from urllib.parse import urlsplit

import aiohttp

from app.domain.exceptions import (
    InvalidUrlError,
    SourceAccessDeniedError,
    SourceNotFoundError,
    SourceRateLimitedError,
)
from app.domain.models import MediaItem, SourcePost
from app.providers.base import BaseProvider


class PixivProvider(BaseProvider):
    name = "pixiv"
    _id_re = re.compile(r"/(?:en/)?artworks/(\d+)")

    def __init__(self, session: aiohttp.ClientSession, cookies: str | None = None) -> None:
        super().__init__(session)
        self.cookies = cookies

    def can_handle(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return host in {"pixiv.net", "www.pixiv.net"} and bool(self._id_re.search(urlsplit(url).path))

    def _source_id(self, url: str) -> str:
        match = self._id_re.search(urlsplit(url).path)
        if not match:
            raise InvalidUrlError("Некорректная ссылка Pixiv")
        return match.group(1)

    def normalize_url(self, url: str) -> str:
        return f"https://www.pixiv.net/en/artworks/{self._source_id(url)}"

    async def _json(self, endpoint: str) -> dict:
        headers = {"Referer": "https://www.pixiv.net/", "Accept": "application/json"}
        if self.cookies:
            headers["Cookie"] = self.cookies
        async with self.session.get(endpoint, headers=headers) as response:
            if response.status == 404:
                raise SourceNotFoundError("Работа Pixiv удалена или не найдена")
            if response.status in {401, 403}:
                raise SourceAccessDeniedError("Публикация Pixiv недоступна без авторизации")
            if response.status == 429:
                raise SourceRateLimitedError("Pixiv временно ограничил запросы")
            response.raise_for_status()
            payload = await response.json(content_type=None)
        if payload.get("error"):
            message = str(payload.get("message", "Pixiv вернул ошибку"))
            if "not found" in message.lower():
                raise SourceNotFoundError(message)
            raise SourceAccessDeniedError(message)
        return payload["body"]

    async def fetch_post(self, url: str) -> SourcePost:
        source_id = self._source_id(url)
        normalized = self.normalize_url(url)
        detail = await self._json(f"https://www.pixiv.net/ajax/illust/{source_id}")
        pages = await self._json(f"https://www.pixiv.net/ajax/illust/{source_id}/pages")
        media = [
            MediaItem(
                url=page["urls"]["original"],
                preview_url=page["urls"].get("regular"),
                filename=f"{source_id}_p{index}{_extension(page['urls']['original'])}",
                order=index,
                width=page.get("width"),
                height=page.get("height"),
                headers={"Referer": "https://www.pixiv.net/"},
            )
            for index, page in enumerate(pages)
        ]
        user_id = str(detail.get("userId", ""))
        return SourcePost(
            provider=self.name,
            source_id=source_id,
            source_url=url,
            normalized_url=normalized,
            title=str(detail.get("illustTitle") or "Без названия"),
            description=str(detail.get("description") or ""),
            author_id=user_id,
            author_name=str(detail.get("userName") or "Неизвестный автор"),
            author_url=f"https://www.pixiv.net/en/users/{user_id}" if user_id else normalized,
            source_tags=[item["tag"] for item in detail.get("tags", {}).get("tags", [])],
            media_items=media,
            metadata={"page_count": len(media)},
        )


def _extension(url: str) -> str:
    suffix = urlsplit(url).path.rsplit("/", 1)[-1].rsplit(".", 1)
    return f".{suffix[-1].lower()}" if len(suffix) == 2 else ".jpg"
