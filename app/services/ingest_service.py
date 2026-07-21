from app.domain.models import SourcePost
from app.providers.registry import ProviderRegistry
from app.utils.input import parse_submission
from app.utils.tags import normalize_tags


class IngestService:
    def __init__(self, registry: ProviderRegistry, max_tags: int, max_tag_length: int) -> None:
        self.registry = registry
        self.max_tags = max_tags
        self.max_tag_length = max_tag_length

    async def ingest(self, text: str) -> tuple[SourcePost, list[str], str | None, int]:
        url, raw_tags, channel, ignored = parse_submission(text)
        provider = self.registry.resolve(url)
        post = await provider.fetch_post(provider.normalize_url(url))
        tags = normalize_tags(raw_tags, self.max_tags, self.max_tag_length)
        return post, tags, channel, ignored
