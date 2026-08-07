"""Extraction primitives for text-first news publications."""

from app.news.classifier import classify_news_input
from app.news.facade import NewsSourceFacade
from app.news.http import PublicOnlyResolver
from app.news.models import (
    ExtractedNewsSource,
    ExtractionProgress,
    ExtractionStage,
    NewsMedia,
    NewsMediaKind,
    NewsSourceKind,
    NewsSourceRequest,
)
from app.news.telegram import ForwardedTelegramPost

__all__ = [
    "ExtractedNewsSource",
    "ExtractionProgress",
    "ExtractionStage",
    "ForwardedTelegramPost",
    "NewsMedia",
    "NewsMediaKind",
    "NewsSourceFacade",
    "NewsSourceKind",
    "NewsSourceRequest",
    "PublicOnlyResolver",
    "classify_news_input",
]
