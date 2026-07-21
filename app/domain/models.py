from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.enums import MediaType


@dataclass(slots=True)
class MediaItem:
    url: str
    filename: str
    order: int
    preview_url: str | None = None
    mime_type: str | None = None
    media_type: MediaType = MediaType.IMAGE
    width: int | None = None
    height: int | None = None
    size: int | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SourcePost:
    provider: str
    source_id: str
    source_url: str
    normalized_url: str
    title: str
    author_name: str
    author_url: str
    media_items: list[MediaItem]
    description: str = ""
    author_id: str | None = None
    source_tags: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    content_warning: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DownloadedMedia:
    source: MediaItem
    path: Path
    mime_type: str
    size: int
    width: int | None = None
    height: int | None = None


@dataclass(slots=True)
class PreparedMedia:
    path: Path
    as_document: bool
    order: int


@dataclass(slots=True)
class PublicationResult:
    chat_id: str
    message_ids: list[int]
    published_at: datetime
    media_count: int
