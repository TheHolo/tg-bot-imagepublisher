from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domain.enums import JobStatus
from app.utils.queue_schedule import estimate_queue_schedule, format_countdown


def make_job(job_id, channel, created_at, *, force=False, status=JobStatus.QUEUED):
    return SimpleNamespace(
        id=job_id, channel=channel, created_at=created_at, force_publish=force,
        status=status, next_attempt_at=None,
    )


def test_queue_slots_are_calculated_independently_per_channel():
    now = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
    first_channel = SimpleNamespace(
        alias="first", publish_interval_seconds=600,
        next_publish_at=now + timedelta(minutes=5),
    )
    second_channel = SimpleNamespace(
        alias="second", publish_interval_seconds=3600,
        next_publish_at=now + timedelta(minutes=20),
    )
    jobs = [
        make_job(1, first_channel, now),
        make_job(2, first_channel, now + timedelta(seconds=1)),
        make_job(3, second_channel, now),
    ]

    schedule = {job.id: estimate for job, estimate in estimate_queue_schedule(jobs, now)}

    assert schedule[1] == now + timedelta(minutes=5)
    assert schedule[2] == now + timedelta(minutes=15)
    assert schedule[3] == now + timedelta(minutes=20)


def test_manual_job_resets_channel_timer_for_regular_jobs():
    now = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
    channel = SimpleNamespace(
        alias="art", publish_interval_seconds=900,
        next_publish_at=now + timedelta(hours=2),
    )
    jobs = [
        make_job(1, channel, now, force=False),
        make_job(2, channel, now + timedelta(seconds=1), force=True),
    ]

    schedule = {job.id: estimate for job, estimate in estimate_queue_schedule(jobs, now)}

    assert schedule[2] == now
    assert schedule[1] == now + timedelta(minutes=15)


def test_countdown_format():
    now = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
    assert format_countdown(now, now) == "сейчас"
    assert format_countdown(now + timedelta(hours=1, minutes=2, seconds=3), now) == "через 1ч 2м 3с"
