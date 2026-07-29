from dataclasses import replace

from app.domain.exceptions import InvalidMediaSelectionError, TooManyMediaError
from app.domain.models import SourcePost
from app.providers.registry import ProviderRegistry
from app.utils.input import parse_submission_batch, split_source_selection
from app.utils.tags import normalize_tags


class IngestService:
    selectable_providers = frozenset({"pixiv", "deviantart"})

    def __init__(
        self,
        registry: ProviderRegistry,
        max_tags: int,
        max_tag_length: int,
        max_urls: int = 10,
        media_limit_enabled: bool = True,
        max_images: int = 10,
    ) -> None:
        self.registry = registry
        self.max_tags = max_tags
        self.max_tag_length = max_tag_length
        self.max_urls = max_urls
        self.media_limit_enabled = media_limit_enabled
        self.max_images = max_images

    def parse(self, text: str) -> tuple[list[str], list[str], str | None]:
        urls, raw_tags, channel = parse_submission_batch(text, self.max_urls)
        tags = normalize_tags(raw_tags, self.max_tags, self.max_tag_length)
        return urls, tags, channel

    async def fetch(self, source: str) -> SourcePost:
        url, selected_numbers = split_source_selection(source)
        provider = self.registry.resolve(url)
        if selected_numbers is not None and provider.name not in self.selectable_providers:
            raise InvalidMediaSelectionError(
                "Выбор отдельных изображений поддерживается только для Pixiv и DeviantArt"
            )
        if (
            self.media_limit_enabled
            and selected_numbers is not None
            and len(selected_numbers) > self.max_images
        ):
            raise TooManyMediaError(
                f"Выбрано {len(selected_numbers)} изображений. Допустимый максимум — {self.max_images}."
            )

        post = await provider.fetch_post(provider.normalize_url(url))
        total = len(post.media_items)
        provider_title = {"pixiv": "Pixiv", "deviantart": "DeviantArt"}.get(
            post.provider, post.provider,
        )
        if selected_numbers is not None:
            missing = [number for number in selected_numbers if number > total]
            if missing:
                missing_text = ", ".join(map(str, missing))
                raise InvalidMediaSelectionError(
                    f"В публикации {provider_title} всего {total} изображений; "
                    f"номера {missing_text} отсутствуют."
                )
            post.media_items = [
                replace(post.media_items[number - 1], order=order)
                for order, number in enumerate(selected_numbers)
            ]
            post.metadata = {
                **post.metadata,
                "original_media_count": total,
                "selected_media_numbers": list(selected_numbers),
            }

        selected_count = len(post.media_items)
        if self.media_limit_enabled and selected_count > self.max_images:
            raise TooManyMediaError(
                f"В публикации {provider_title} {selected_count} изображений. "
                f"Допустимый максимум — {self.max_images}. "
                "Укажите нужные номера после ссылки, например [1,3,5-7]."
            )
        return post
