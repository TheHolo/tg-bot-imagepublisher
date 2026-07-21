from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import Channel, Job, JobEvent, Publication, User, utcnow
from app.domain.enums import ACTIVE_JOB_STATUSES, JobStatus
from app.domain.models import SourcePost


def serialize_post(post: SourcePost) -> dict:
    return {
        "provider": post.provider,
        "source_id": post.source_id,
        "source_url": post.source_url,
        "normalized_url": post.normalized_url,
        "title": post.title,
        "description": post.description,
        "author_id": post.author_id,
        "author_name": post.author_name,
        "author_url": post.author_url,
        "source_tags": post.source_tags,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "media_items": [
            {
                "url": item.url,
                "preview_url": item.preview_url,
                "filename": item.filename,
                "mime_type": item.mime_type,
                "media_type": item.media_type.value,
                "width": item.width,
                "height": item.height,
                "size": item.size,
                "order": item.order,
                "headers": item.headers,
            }
            for item in post.media_items
        ],
        "metadata": post.metadata,
    }


class JobService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def ensure_user(self, telegram_id: int, username: str | None, display_name: str) -> User:
        async with self.sessions() as session, session.begin():
            user = await session.scalar(select(User).where(User.telegram_user_id == telegram_id))
            if user is None:
                user = User(telegram_user_id=telegram_id, username=username, display_name=display_name)
                session.add(user)
                await session.flush()
            else:
                user.username = username
                user.display_name = display_name
                user.last_seen_at = utcnow()
            return user

    async def get_channel(self, alias: str | None) -> Channel | None:
        async with self.sessions() as session:
            statement = select(Channel).where(Channel.is_enabled.is_(True))
            if alias:
                statement = statement.where(Channel.alias == alias)
            else:
                statement = statement.where(Channel.is_default.is_(True))
            return await session.scalar(statement)

    async def channels(self) -> list[Channel]:
        async with self.sessions() as session:
            return list((await session.scalars(select(Channel).order_by(Channel.alias))).all())

    async def set_channel_interval(self, alias: str, seconds: int) -> Channel | None:
        async with self.sessions() as session, session.begin():
            channel = await session.scalar(select(Channel).where(Channel.alias == alias, Channel.is_enabled.is_(True)))
            if not channel:
                return None
            channel.publish_interval_seconds = seconds
            last_published = await session.scalar(
                select(func.max(Publication.published_at)).where(Publication.channel_id == channel.id)
            )
            channel.next_publish_at = last_published + timedelta(seconds=seconds) if last_published and seconds else None
            return channel

    async def create_preview(
        self, user_id: int, post: SourcePost, channel_id: int, tags: list[str], max_attempts: int
    ) -> Job:
        async with self.sessions() as session, session.begin():
            job = Job(
                created_by_user_id=user_id,
                provider=post.provider,
                source_id=post.source_id,
                source_url=post.source_url,
                normalized_url=post.normalized_url,
                target_channel_id=channel_id,
                status=JobStatus.WAITING_CONFIRMATION,
                user_tags=tags,
                source_tags=post.source_tags,
                post_data=serialize_post(post),
                max_attempts=max_attempts,
            )
            session.add(job)
            await session.flush()
            return job

    async def get(self, job_id: int) -> Job | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(Job).options(selectinload(Job.channel), selectinload(Job.media_items)).where(Job.id == job_id)
            )

    async def next_queued(self) -> Job | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(Job)
                .join(Channel, Job.target_channel_id == Channel.id)
                .options(selectinload(Job.channel))
                .where(
                    Job.status == JobStatus.QUEUED,
                    Job.cancel_requested.is_(False),
                    Channel.is_enabled.is_(True),
                )
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )

    async def transition(self, job_id: int, status: JobStatus, message: str | None = None) -> Job | None:
        async with self.sessions() as session, session.begin():
            job = await session.get(Job, job_id)
            if not job:
                return None
            old = job.status
            job.status = status
            if status == JobStatus.DOWNLOADING and not job.started_at:
                job.started_at = utcnow()
            if status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                job.finished_at = utcnow()
            session.add(JobEvent(job_id=job_id, event_type="status", old_status=old, new_status=status, message=message))
            return job

    async def enqueue(self, job_id: int) -> bool:
        async with self.sessions() as session, session.begin():
            job = await session.get(Job, job_id)
            if not job or job.status not in {JobStatus.WAITING_CONFIRMATION, JobStatus.FAILED}:
                return False
            if job.status == JobStatus.FAILED:
                job.attempts = 0
            job.status = JobStatus.QUEUED
            job.cancel_requested = False
            job.error_code = job.error_message = None
            return True

    async def claim_next(self) -> Job | None:
        async with self.sessions() as session, session.begin():
            candidate = (
                select(Job.id)
                .join(Channel, Job.target_channel_id == Channel.id)
                .where(
                    Job.status == JobStatus.QUEUED,
                    Job.cancel_requested.is_(False),
                    or_(Job.next_attempt_at.is_(None), Job.next_attempt_at <= utcnow()),
                    Channel.is_enabled.is_(True),
                    or_(
                        Job.force_publish.is_(True),
                        Channel.next_publish_at.is_(None),
                        Channel.next_publish_at <= utcnow(),
                    ),
                )
                .order_by(Job.force_publish.desc(), Job.created_at)
                .limit(1)
            )
            claimed_id = await session.scalar(
                update(Job)
                .where(Job.id == candidate.scalar_subquery(), Job.status == JobStatus.QUEUED)
                .values(
                    status=JobStatus.DOWNLOADING, attempts=Job.attempts + 1,
                    started_at=func.coalesce(Job.started_at, utcnow()), next_attempt_at=None,
                )
                .returning(Job.id)
            )
            if claimed_id is None:
                return None
            return await session.scalar(select(Job).options(selectinload(Job.channel)).where(Job.id == claimed_id))

    async def force_publish(self, job_id: int) -> Job | None:
        async with self.sessions() as session, session.begin():
            job = await session.get(Job, job_id)
            if not job or job.status != JobStatus.QUEUED or job.cancel_requested:
                return None
            job.force_publish = True
            session.add(JobEvent(
                job_id=job.id, event_type="manual_publish", old_status=job.status,
                new_status=job.status, message="Manual publication requested",
            ))
            return job

    async def force_next_publish(self) -> Job | None:
        async with self.sessions() as session, session.begin():
            candidate = (
                select(Job.id)
                .join(Channel, Job.target_channel_id == Channel.id)
                .where(
                    Job.status == JobStatus.QUEUED,
                    Job.cancel_requested.is_(False),
                    Channel.is_enabled.is_(True),
                )
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )
            forced_id = await session.scalar(
                update(Job)
                .where(Job.id == candidate.scalar_subquery(), Job.status == JobStatus.QUEUED)
                .values(force_publish=True)
                .returning(Job.id)
            )
            if forced_id is None:
                return None
            job = await session.get(Job, forced_id)
            session.add(JobEvent(
                job_id=job.id, event_type="manual_publish", old_status=job.status,
                new_status=job.status, message="Manual publication requested for next queued job",
            ))
            return job

    async def request_cancel(self, job_id: int) -> bool:
        async with self.sessions() as session, session.begin():
            job = await session.get(Job, job_id)
            if not job or job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED}:
                return False
            if job.status in {JobStatus.WAITING_CONFIRMATION, JobStatus.QUEUED, JobStatus.FAILED}:
                job.status = JobStatus.CANCELLED
                job.finished_at = utcnow()
            else:
                job.cancel_requested = True
            return True

    async def is_cancelled(self, job_id: int) -> bool:
        async with self.sessions() as session:
            job = await session.get(Job, job_id)
            return not job or job.cancel_requested or job.status == JobStatus.CANCELLED

    async def fail(self, job_id: int, error: Exception, retry: bool) -> None:
        async with self.sessions() as session, session.begin():
            job = await session.get(Job, job_id)
            if not job:
                return
            job.error_code = getattr(error, "code", type(error).__name__)
            job.error_message = str(error)[:2000]
            job.status = JobStatus.QUEUED if retry and job.attempts < job.max_attempts else JobStatus.FAILED
            if job.status == JobStatus.QUEUED:
                delays = (5, 30, 120)
                job.next_attempt_at = utcnow() + timedelta(seconds=delays[min(job.attempts - 1, len(delays) - 1)])
            if job.status == JobStatus.FAILED:
                job.finished_at = utcnow()

    async def change_channel(self, job_id: int, alias: str) -> bool:
        async with self.sessions() as session, session.begin():
            job = await session.get(Job, job_id)
            channel = await session.scalar(select(Channel).where(Channel.alias == alias, Channel.is_enabled.is_(True)))
            if not job or not channel or job.status != JobStatus.WAITING_CONFIRMATION:
                return False
            job.target_channel_id = channel.id
            return True

    async def duplicate(self, job: Job) -> Publication | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(Publication)
                .join(Job, Publication.job_id == Job.id)
                .where(Job.provider == job.provider, Job.source_id == job.source_id, Publication.channel_id == job.target_channel_id)
                .limit(1)
            )

    async def duplicate_for(self, provider: str, source_id: str, channel_id: int) -> Publication | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(Publication)
                .join(Job, Publication.job_id == Job.id)
                .where(Job.provider == provider, Job.source_id == source_id, Publication.channel_id == channel_id)
                .limit(1)
            )

    async def allow_duplicate_and_enqueue(self, job_id: int) -> bool:
        async with self.sessions() as session, session.begin():
            job = await session.get(Job, job_id)
            if not job or job.status != JobStatus.WAITING_CONFIRMATION:
                return False
            job.allow_duplicate = True
            job.status = JobStatus.QUEUED
            return True

    async def queue(self, limit: int = 10) -> list[Job]:
        async with self.sessions() as session:
            return list(
                (await session.scalars(select(Job).where(Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.created_at).limit(limit))).all()
            )

    async def recent(self, limit: int = 10) -> list[Job]:
        async with self.sessions() as session:
            return list((await session.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit))).all())

    async def stats(self) -> dict[str, int]:
        async with self.sessions() as session:
            rows = (await session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status))).all()
            return {str(status): count for status, count in rows}

    async def recover(self, stale_minutes: int = 15) -> int:
        cutoff = utcnow() - timedelta(minutes=stale_minutes)
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                update(Job)
                .where(Job.status.in_({JobStatus.DOWNLOADING, JobStatus.PROCESSING}), Job.updated_at < cutoff)
                .values(status=JobStatus.QUEUED, error_message="Recovered after restart")
            )
            # Publishing is deliberately not replayed automatically: that could duplicate posts.
            await session.execute(
                update(Job)
                .where(Job.status == JobStatus.PUBLISHING, Job.updated_at < cutoff)
                .values(status=JobStatus.FAILED, error_code="uncertain_publish", error_message="Проверьте канал перед повтором")
            )
            return result.rowcount
