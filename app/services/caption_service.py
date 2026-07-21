from html import escape

from app.domain.exceptions import MediaValidationError
from app.domain.models import SourcePost
from app.utils.tags import hashtags


DEFAULT_TEMPLATE = (
    '<b>{title}</b>\n\nАвтор: <a href="{author_url}">{author_name}</a>\n'
    'Источник: <a href="{source_url}">{provider_name}</a>\n\n{hashtags}'
)


class CaptionService:
    def __init__(self, limit: int = 1024) -> None:
        self.limit = limit

    def build(self, post: SourcePost, tags: list[str], template: str = DEFAULT_TEMPLATE) -> str:
        values = {
            "title": escape(post.title or "Без названия"),
            "author_name": escape(post.author_name or "Неизвестный автор"),
            "author_url": escape(post.author_url, quote=True),
            "source_url": escape(post.normalized_url, quote=True),
            "provider_name": escape(post.provider.title()),
            "hashtags": hashtags(tags),
        }
        caption = template.format_map(values)
        if len(caption) <= self.limit:
            return caption
        # Keep valid HTML and the required links; shorten only plain title and tags.
        values["hashtags"] = ""
        over = len(template.format_map(values)) - self.limit
        if over > 0:
            values["title"] = values["title"][: max(1, len(values["title"]) - over - 1)] + "…"
        caption = template.format_map(values).rstrip()
        if len(caption) > self.limit:
            raise MediaValidationError("Обязательные поля подписи превышают лимит Telegram")
        return caption
