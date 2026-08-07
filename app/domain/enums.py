from enum import StrEnum


class JobStatus(StrEnum):
    CREATED = "created"
    RESOLVING = "resolving"
    WAITING_CONFIRMATION = "waiting_confirmation"
    QUEUED = "queued"
    SCHEDULED = "scheduled"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.SCHEDULED,
    JobStatus.DOWNLOADING,
    JobStatus.PROCESSING,
    JobStatus.PUBLISHING,
}


class MediaType(StrEnum):
    IMAGE = "image"
    ANIMATION = "animation"
    VIDEO = "video"
    DOCUMENT = "document"


class ContentKind(StrEnum):
    ARTWORK = "artwork"
    NEWS = "news"


class NewsTaskStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishMode(StrEnum):
    PHOTO = "photo"
    DOCUMENT = "document"
    AUTO = "auto"
