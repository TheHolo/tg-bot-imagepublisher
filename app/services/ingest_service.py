from app.domain.models import SourcePost
from app.providers.registry import ProviderRegistry
from app.utils.input import parse_submission_batch
from app.utils.tags import normalize_tags


class IngestService:
    def __init__(self, registry: ProviderRegistry, max_tags: int, max_tag_length: int, max_urls: int = 10) -> None:
        self.registry = registry
        self.max_tags = max_tags
        self.max_tag_length = max_tag_length
        self.max_urls = max_urls

    def parse(self, text: str) -> tuple[list[str], list[str], str | None]:
        urls, raw_tags, channel = parse_submission_batch(text, self.max_urls)
        tags = normalize_tags(raw_tags, self.max_tags, self.max_tag_length)
        return urls, tags, channel

    async def fetch(self, url: str) -> SourcePost:
        provider = self.registry.resolve(url)
        return await provider.fetch_post(provider.normalize_url(url))
