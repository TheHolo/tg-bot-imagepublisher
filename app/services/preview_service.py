import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.db.models import Job
from app.domain.enums import MediaType
from app.domain.models import MediaItem, SourcePost
from app.services.caption_service import CaptionService, DEFAULT_TEMPLATE
from app.services.download_service import DownloadService
from app.services.media_service import MediaService
from app.services.publisher_service import TelegramPublisher
from app.services.translation_service import TranslationService
from app.utils.tags import merge_tags


def deserialize_post(data: dict) -> SourcePost:
    items = [MediaItem(**{**item, "media_type": MediaType(item.get("media_type", "image"))}) for item in data["media_items"]]
    return SourcePost(
        provider=data["provider"], source_id=data["source_id"], source_url=data["source_url"],
        normalized_url=data["normalized_url"], title=data["title"], description=data.get("description", ""),
        author_id=data.get("author_id"), author_name=data["author_name"], author_url=data["author_url"],
        source_tags=data.get("source_tags", []), media_items=items,
        published_at=datetime.fromisoformat(data["published_at"]) if data.get("published_at") else None,
        metadata=data.get("metadata", {}),
    )


class PreviewService:
    def __init__(
        self, *, downloader: DownloadService, media: MediaService, captions: CaptionService,
        publisher: TelegramPublisher, storage: Path, auto_add_source_tags: bool,
        max_tags: int, max_tag_length: int, translator: TranslationService,
    ) -> None:
        self.downloader = downloader
        self.media = media
        self.captions = captions
        self.publisher = publisher
        self.translator = translator
        self.storage = storage
        self.auto_add_source_tags = auto_add_source_tags
        self.max_tags = max_tags
        self.max_tag_length = max_tag_length

    async def send(self, job: Job, chat_id: int | str) -> None:
        post = deserialize_post(job.post_data)
        await self.translator.enrich_title(post)
        temporary_id = f"preview-{job.id}-{uuid4().hex}"
        try:
            downloaded = [await self.downloader.download(temporary_id, item) for item in post.media_items]
            prepared = [await self.media.prepare(item, job.channel.publish_mode) for item in downloaded]
            caption_tags = job.user_tags
            if self.auto_add_source_tags:
                caption_tags = merge_tags(job.user_tags, job.source_tags, self.max_tags, self.max_tag_length)
            caption = self.captions.build(post, caption_tags, job.channel.caption_template or DEFAULT_TEMPLATE)
            await self.publisher.preview(chat_id, prepared, caption)
        finally:
            shutil.rmtree(self.storage / "jobs" / temporary_id, ignore_errors=True)
