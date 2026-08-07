from app.domain.exceptions import ApplicationError


class NewsExtractionError(ApplicationError):
    code = "news_extraction_error"


class InvalidNewsInputError(NewsExtractionError):
    code = "invalid_news_input"


class UnsafeNewsUrlError(InvalidNewsInputError):
    code = "unsafe_news_url"


class UnsupportedNewsSourceError(NewsExtractionError):
    code = "unsupported_news_source"


class NewsSourceNotFoundError(NewsExtractionError):
    code = "news_source_not_found"


class NewsSourceAccessError(NewsExtractionError):
    code = "news_source_access_error"


class NewsSourceRateLimitedError(NewsExtractionError):
    code = "news_source_rate_limited"
    retryable = True


class NewsSourceUnavailableError(NewsExtractionError):
    code = "news_source_unavailable"
    retryable = True


class NewsContentTooLargeError(NewsExtractionError):
    code = "news_content_too_large"


class EmptyNewsContentError(NewsExtractionError):
    code = "empty_news_content"


class NewsTranscriptUnavailableError(NewsExtractionError):
    code = "news_transcript_unavailable"


class MissingNewsDependencyError(NewsExtractionError):
    code = "missing_news_dependency"
