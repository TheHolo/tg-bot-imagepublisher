from html import escape

from app.domain.exceptions import MediaValidationError
from app.domain.models import SourcePost
from app.utils.tags import hashtags
from app.utils.text import provider_label, shorten


DEFAULT_TEMPLATE = (
    '🖼 <b>{title}</b>\n\n{description_block}'
    '🎨 <a href="{author_url}">{author_name}</a>\n'
    '🔗 <a href="{source_url}">{source_label}</a>\n'
    '{published_at_block}\n{hashtags_block}'
)


class CaptionService:
    def __init__(self, limit: int = 1024) -> None:
        self.limit = limit

    def build(self, post: SourcePost, tags: list[str], template: str = DEFAULT_TEMPLATE) -> str:
        title = post.title or "Без названия"
        translated_title = str(post.metadata.get("title_translation") or "").strip()
        if translated_title and translated_title.casefold() != title.casefold():
            title = f"{title} (TL: {translated_title})"
        description = shorten(post.description, 240)
        current_tags = list(tags)

        def render() -> str:
            provider_name = provider_label(post.provider)
            values = {
            "title": escape(title),
            "author_name": escape(post.author_name or "Неизвестный автор"),
            "author_url": escape(post.author_url, quote=True),
            "source_url": escape(post.normalized_url, quote=True),
            "provider_name": escape(provider_name),
            "source_label": escape(f"Оригинал на {provider_name}" if post.provider != "direct" else "Открыть оригинал"),
            "description_block": f"{escape(description)}\n\n" if description else "",
            "published_at_block": f"📅 {post.published_at:%d.%m.%Y}\n" if post.published_at else "",
            "hashtags": hashtags(current_tags),
            "hashtags_block": f"🏷 {hashtags(current_tags)}" if current_tags else "",
            }
            return template.format_map(values).rstrip()

        caption = render()
        if len(caption) <= self.limit:
            return caption
        while description and len(caption) > self.limit:
            description = shorten(description, max(0, len(description) - (len(caption) - self.limit) - 1))
            caption = render()
        if len(caption) > self.limit:
            current_tags = []
            caption = render()
        while len(title) > 1 and len(caption) > self.limit:
            target_length = max(1, len(title) - (len(caption) - self.limit))
            shortened_title = "…" if target_length == 1 else title[: target_length - 1].rstrip() + "…"
            if shortened_title == title:
                break
            title = shortened_title
            caption = render()
        if len(caption) > self.limit:
            raise MediaValidationError("Обязательные поля подписи превышают лимит Telegram")
        return caption
