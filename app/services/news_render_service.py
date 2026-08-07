from html import escape

from app.domain.exceptions import PublishError
from app.domain.models import SourcePost
from app.utils.tags import hashtags

MAX_NEWS_MESSAGE_LENGTH = 4096


class NewsRenderService:
    """Build Telegram-safe HTML for a news publication."""

    def build(self, post: SourcePost, tags: list[str]) -> str:
        headline = post.title.strip() or "Без заголовка"
        lead = post.description.strip()
        body = post.body.strip()
        source_label = post.author_name.strip() or post.provider or "Источник"

        plain_parts = [headline]
        html_parts = [f"<b>{escape(headline)}</b>"]
        if lead and lead != body:
            plain_parts.append(lead)
            html_parts.append(escape(lead))
        if body:
            plain_parts.append(body)
            html_parts.append(escape(body))
        if post.source_url:
            # Telegram counts the visible anchor text, not the href target, toward
            # the post length after parsing HTML entities.
            source_plain = f"Источник: {source_label}"
            source_html = (
                f'Источник: <a href="{escape(post.source_url, quote=True)}">'
                f"{escape(source_label)}</a>"
            )
            plain_parts.append(source_plain)
            html_parts.append(source_html)
        rendered_tags = hashtags(tags)
        if rendered_tags:
            plain_parts.append(rendered_tags)
            html_parts.append(rendered_tags)

        self._validate_plain("\n\n".join(plain_parts))
        return "\n\n".join(html_parts)

    def build_custom(
        self, value: str, post: SourcePost | None = None, tags: list[str] | None = None,
    ) -> str:
        value = value.strip()
        plain_parts = [value]
        html_parts = [escape(value)]
        if post is not None and post.source_url:
            label = post.author_name.strip() or post.provider or "Источник"
            plain_parts.append(f"Источник: {label}")
            html_parts.append(
                f'Источник: <a href="{escape(post.source_url, quote=True)}">'
                f"{escape(label)}</a>"
            )
        rendered_tags = hashtags(tags or [])
        if rendered_tags:
            plain_parts.append(rendered_tags)
            html_parts.append(rendered_tags)
        self._validate_plain("\n\n".join(plain_parts))
        return "\n\n".join(html_parts)

    def validate_custom(self, value: str) -> None:
        self._validate_plain(value.strip())

    @staticmethod
    def _validate_plain(value: str) -> None:
        if not value:
            raise PublishError("Текст новости не может быть пустым")
        if len(value) > MAX_NEWS_MESSAGE_LENGTH:
            raise PublishError(
                f"Текст новости превышает лимит Telegram {MAX_NEWS_MESSAGE_LENGTH} символов"
            )
