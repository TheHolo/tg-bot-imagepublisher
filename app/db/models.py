from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import ContentKind, JobStatus, NewsTaskStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    last_selected_channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    publish_mode: Mapped[str] = mapped_column(String(16), default="auto")
    publish_interval_seconds: Mapped[int] = mapped_column(Integer, default=0)
    next_publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_job_id: Mapped[int | None] = mapped_column(Integer, index=True)
    caption_template: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChannelMemberSnapshot(Base):
    __tablename__ = "channel_member_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "channel_id", "source", "snapshot_date",
            name="uq_channel_member_snapshot_daily_source",
        ),
        Index(
            "ix_channel_member_snapshots_channel_captured",
            "channel_id", "captured_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    member_count: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))
    snapshot_date: Mapped[date | None] = mapped_column(Date)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    content_kind: Mapped[str] = mapped_column(
        String(32), default=ContentKind.ARTWORK, index=True,
    )
    source_id: Mapped[str] = mapped_column(String(255), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str] = mapped_column(Text)
    target_channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.WAITING_CONFIRMATION, index=True)
    user_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    post_data: Mapped[dict] = mapped_column(JSON, default=dict)
    caption_override: Mapped[str | None] = mapped_column(Text)
    queue_position: Mapped[int | None] = mapped_column(Integer, index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    force_publish: Mapped[bool] = mapped_column(Boolean, default=False)
    channel: Mapped[Channel] = relationship()
    media_items: Mapped[list["MediaRecord"]] = relationship(cascade="all, delete-orphan")


class NewsTask(Base):
    __tablename__ = "news_tasks"
    __table_args__ = (
        Index("ix_news_tasks_claim", "status", "lease_expires_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    origin_chat_id: Mapped[str] = mapped_column(String(64))
    status_message_id: Mapped[int | None] = mapped_column(Integer)
    source_kind: Mapped[str] = mapped_column(String(32), index=True)
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    user_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(
        String(32), default=NewsTaskStatus.QUEUED, index=True,
    )
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    stage_message: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(128))
    lease_owner: Mapped[str | None] = mapped_column(String(128), index=True)
    lease_token: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel: Mapped[Channel] = relationship()
    job: Mapped[Job | None] = relationship()


class MediaRecord(Base):
    __tablename__ = "media_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    prepared_path: Mapped[str | None] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    media_type: Mapped[str] = mapped_column(String(32), default="image")
    size: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer)
    download_status: Mapped[str] = mapped_column(String(32), default="pending")
    publish_status: Mapped[str] = mapped_column(String(32), default="pending")
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("job_id", "channel_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(64))
    telegram_message_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    caption: Mapped[str] = mapped_column(Text)


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    old_status: Mapped[str | None] = mapped_column(String(32))
    new_status: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
