from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.db.models import Job
from app.domain.enums import JobStatus

IN_PROGRESS_STATUSES = {
    JobStatus.DOWNLOADING,
    JobStatus.PROCESSING,
    JobStatus.PUBLISHING,
}


def estimate_queue_schedule(jobs: list[Job], now: datetime | None = None) -> list[tuple[Job, datetime]]:
    """Estimate publication slots independently for each channel."""
    now = _aware_utc(now or datetime.now(timezone.utc))
    grouped: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        grouped[job.channel.alias].append(job)

    result: list[tuple[Job, datetime]] = []
    for alias in sorted(grouped):
        channel_jobs = sorted(grouped[alias], key=_job_priority)
        channel = channel_jobs[0].channel
        interval = timedelta(seconds=channel.publish_interval_seconds)
        next_channel_slot = max(now, _aware_utc(channel.next_publish_at or now))

        immediate_jobs = [
            job for job in channel_jobs
            if job.status in IN_PROGRESS_STATUSES or job.force_publish
        ]
        regular_jobs = [job for job in channel_jobs if job not in immediate_jobs]

        latest_immediate: datetime | None = None
        for job in immediate_jobs:
            estimate = max(now, _aware_utc(job.next_attempt_at or now))
            result.append((job, estimate))
            latest_immediate = max(latest_immediate or estimate, estimate)

        if latest_immediate is not None:
            # A manual/in-progress publication resets this channel's old timer.
            next_channel_slot = latest_immediate + interval

        for job in regular_jobs:
            estimate = max(next_channel_slot, _aware_utc(job.next_attempt_at or now))
            result.append((job, estimate))
            next_channel_slot = estimate + interval

    # Merge independently calculated channel schedules into one chronological
    # queue. This keeps /queue useful when one channel has many more jobs than
    # another and gives deterministic ordering for equal publication times.
    return sorted(
        result,
        key=lambda item: (
            _aware_utc(item[1]),
            _aware_utc(item[0].created_at),
            item[0].id,
        ),
    )


def next_queued_by_schedule(
    jobs: list[Job], now: datetime | None = None,
) -> tuple[Job, datetime] | None:
    """Return the queued job with the earliest estimated publication time."""
    candidates = [
        (job, estimate)
        for job, estimate in estimate_queue_schedule(jobs, now)
        if job.status == JobStatus.QUEUED
    ]
    return min(
        candidates,
        key=lambda item: (_aware_utc(item[1]), _aware_utc(item[0].created_at), item[0].id),
        default=None,
    )


def format_countdown(target: datetime, now: datetime | None = None) -> str:
    now = _aware_utc(now or datetime.now(timezone.utc))
    seconds = max(0, round((_aware_utc(target) - now).total_seconds()))
    if seconds == 0:
        return "сейчас"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes:
        parts.append(f"{minutes}м")
    if seconds and not days:
        parts.append(f"{seconds}с")
    return "через " + " ".join(parts)


def _job_priority(job: Job) -> tuple[int, datetime, int]:
    if job.status in IN_PROGRESS_STATUSES:
        priority = 0
    elif job.force_publish:
        priority = 1
    else:
        priority = 2
    return priority, _aware_utc(job.created_at), job.id


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
