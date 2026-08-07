from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin

from app.news.errors import (
    EmptyNewsContentError,
    MissingNewsDependencyError,
    UnsafeNewsUrlError,
)
from app.news.http import SafeHttpFetcher
from app.news.models import (
    ExtractedNewsSource,
    ExtractionProgress,
    ExtractionStage,
    NewsMedia,
    NewsMediaKind,
    NewsSourceKind,
    ProgressCallback,
)

ContentExtractor = Callable[[str, str], Mapping[str, Any]]
MetadataExtractor = Callable[[str, str], Mapping[str, Any]]


class WebsiteExtractor:
    def __init__(
        self,
        fetcher: SafeHttpFetcher,
        *,
        content_extractor: ContentExtractor | None = None,
        metadata_extractor: MetadataExtractor | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._content_extractor = content_extractor or _extract_with_trafilatura
        self._metadata_extractor = metadata_extractor or _extract_with_extruct

    async def extract(
        self,
        url: str,
        *,
        progress: ProgressCallback | None = None,
    ) -> ExtractedNewsSource:
        await _emit(
            progress,
            ExtractionStage.EXTRACTING_CONTENT,
            "Парсим новость с Trafilatura",
        )
        page = await self._fetcher.fetch_text(url)
        content_payload, metadata_payload = await asyncio.gather(
            asyncio.to_thread(self._content_extractor, page.text, page.final_url),
            asyncio.to_thread(self._metadata_extractor, page.text, page.final_url),
        )
        content = dict(content_payload or {})
        structured = dict(metadata_payload or {})
        records = list(_metadata_records(structured))

        raw_text = _clean_text(_first(content, "text", "raw_text", "body"))
        if not raw_text:
            raw_text = _clean_text(_record_value(records, "articleBody", "text"))
        if not raw_text:
            raise EmptyNewsContentError(
                "Не удалось извлечь текст статьи; вставьте текст вручную"
            )

        title = _clean_inline(
            _first(content, "title", "headline")
            or _record_value(records, "headline", "name", "og:title")
        )
        if not title:
            title = _clean_inline(raw_text.splitlines()[0])[:180] or "Новость"
        author_name, author_url = _author(
            _first(content, "author"),
            _record_value(records, "author"),
        )
        published_at = _parse_datetime(
            _first(content, "date", "published_at")
            or _record_value(
                records, "datePublished", "dateCreated", "article:published_time"
            )
        )
        image_url = _image_url(
            _first(content, "image"),
            _record_value(records, "image", "thumbnailUrl", "og:image"),
            page.final_url,
        )
        media: list[NewsMedia] = []
        if image_url:
            try:
                await self._fetcher.ensure_safe_url(image_url)
            except UnsafeNewsUrlError:
                image_url = None
            if image_url:
                media.append(NewsMedia(kind=NewsMediaKind.IMAGE, url=image_url))

        if author_url:
            author_url = urljoin(page.final_url, author_url)
            try:
                await self._fetcher.ensure_safe_url(author_url)
            except UnsafeNewsUrlError:
                author_url = None

        description = _clean_inline(
            _first(content, "description")
            or _record_value(records, "description", "og:description")
        )
        site_name = _clean_inline(
            _first(content, "sitename", "hostname")
            or _record_value(records, "publisher", "og:site_name")
        )
        metadata: dict[str, Any] = {
            "extractor": "trafilatura+extruct",
            "description": description or None,
            "site_name": site_name or None,
        }
        tags = _tags(_first(content, "tags", "categories", "keywords"))
        if tags:
            metadata["tags"] = tags

        return ExtractedNewsSource(
            kind=NewsSourceKind.WEBSITE,
            source_id=_source_id(page.final_url),
            source_url=url,
            normalized_url=page.final_url,
            title=title[:500],
            raw_text=raw_text,
            author_name=author_name,
            author_url=author_url,
            published_at=published_at,
            media=media,
            metadata=metadata,
        )


def _extract_with_trafilatura(html: str, url: str) -> Mapping[str, Any]:
    try:
        import trafilatura
    except ImportError as exc:
        raise MissingNewsDependencyError(
            "Для обработки сайтов установите зависимость trafilatura"
        ) from exc
    payload = trafilatura.extract(
        html,
        url=url,
        output_format="json",
        with_metadata=True,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if not payload:
        return {}
    try:
        result = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return {"text": str(payload)}
    return result if isinstance(result, dict) else {}


def _extract_with_extruct(html: str, url: str) -> Mapping[str, Any]:
    try:
        import extruct
    except ImportError as exc:
        raise MissingNewsDependencyError(
            "Для обработки метаданных сайтов установите зависимость extruct"
        ) from exc
    try:
        return extruct.extract(
            html,
            base_url=url,
            syntaxes=["json-ld", "opengraph", "microdata"],
            uniform=True,
        )
    except (KeyError, TypeError, ValueError):
        return {}


async def _emit(
    progress: ProgressCallback | None,
    stage: ExtractionStage,
    message: str,
) -> None:
    if progress is not None:
        await progress(ExtractionProgress(stage=stage, message=message))


def _metadata_records(payload: Mapping[str, Any]):
    for syntax in ("json-ld", "opengraph", "microdata"):
        value = payload.get(syntax) or []
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                yield from (entry for entry in graph if isinstance(entry, dict))
            yield item
            properties = item.get("properties")
            if isinstance(properties, dict):
                yield properties


def _record_value(records: list[Mapping[str, Any]], *keys: str) -> Any:
    for record in records:
        for key in keys:
            value = record.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _author(primary: Any, fallback: Any) -> tuple[str | None, str | None]:
    value = primary or fallback
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        return (
            _clean_inline(value.get("name")) or None,
            _optional_string(value.get("url") or value.get("@id")),
        )
    result = _clean_inline(value)
    return (result or None, None)


def _image_url(primary: Any, fallback: Any, base_url: str) -> str | None:
    value = primary or fallback
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or value.get("@id")
    result = _optional_string(value)
    return urljoin(base_url, result) if result else None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _clean_text(value: Any) -> str:
    if not value:
        return ""
    lines = [" ".join(unescape(line).split()) for line in str(value).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _clean_inline(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("value")
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return " ".join(unescape(str(value or "")).split())


def _optional_string(value: Any) -> str | None:
    result = str(value).strip() if value is not None else ""
    return result or None


def _tags(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        return []
    return [tag for item in values if (tag := _clean_inline(item))]


def _source_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
