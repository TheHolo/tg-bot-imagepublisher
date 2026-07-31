from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.domain.enums import JobStatus
from app.utils.queue_schedule import (
    estimate_queue_schedule,
    format_countdown,
    next_queued_by_schedule,
    regular_queue_completion,
)


def make_job(
    job_id, channel, created_at, *, force=False, status=JobStatus.QUEUED,
    queue_position=None, scheduled_at=None,
):
    return SimpleNamespace(
        id=job_id, channel=channel, created_at=created_at, force_publish=force,
        status=status, next_attempt_at=None, queue_position=queue_position,
        scheduled_at=scheduled_at,
    )


def test_queue_slots_are_calculated_independently_per_channel():
    now = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)
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


def test_queue_schedule_is_merged_chronologically_across_channels():
    now = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)
    arknights = SimpleNamespace(
        alias="arknights", publish_interval_seconds=1800,
        next_publish_at=now + timedelta(minutes=25),
    )
    spice_and_wolf = SimpleNamespace(
        alias="spice_and_wolf", publish_interval_seconds=7200,
        next_publish_at=now + timedelta(minutes=30),
    )
    jobs = [
        *[
            make_job(job_id, arknights, now + timedelta(seconds=job_id))
            for job_id in range(1, 52)
        ],
        make_job(100, spice_and_wolf, now),
    ]

    schedule = estimate_queue_schedule(jobs, now)

    assert [job.id for job, _ in schedule[:3]] == [1, 100, 2]
    assert any(job.channel.alias == "spice_and_wolf" for job, _ in schedule[:50])


def test_manual_job_resets_channel_timer_for_regular_jobs():
    now = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)
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
    now = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)
    assert format_countdown(now, now) == "сейчас"
    assert format_countdown(now + timedelta(hours=1, minutes=2, seconds=3), now) == "через 1ч 2м 3с"


def test_next_queued_uses_earliest_estimated_time_across_channels():
    now = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)
    arknights = SimpleNamespace(
        alias="arknights", publish_interval_seconds=7200,
        next_publish_at=now + timedelta(minutes=12),
    )
    spice_and_wolf = SimpleNamespace(
        alias="spice_and_wolf", publish_interval_seconds=7200,
        next_publish_at=now + timedelta(hours=2),
    )
    jobs = [
        make_job(17, spice_and_wolf, now - timedelta(hours=1)),
        make_job(28, arknights, now),
    ]

    selected = next_queued_by_schedule(jobs, now)

    assert selected is not None
    assert selected[0].id == 28
    assert selected[1] == now + timedelta(minutes=12)


def test_next_queued_returns_none_when_only_in_progress_jobs_exist():
    now = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)
    channel = SimpleNamespace(alias="art", publish_interval_seconds=60, next_publish_at=None)
    jobs = [make_job(1, channel, now, status=JobStatus.PUBLISHING)]

    assert next_queued_by_schedule(jobs, now) is None


def test_queue_position_and_exact_time_affect_estimated_order():
    now = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)
    channel = SimpleNamespace(
        alias="art", publish_interval_seconds=600, next_publish_at=None,
    )
    jobs = [
        make_job(1, channel, now, queue_position=2),
        make_job(
            2, channel, now + timedelta(seconds=1), queue_position=1,
            status=JobStatus.SCHEDULED,
            scheduled_at=now + timedelta(hours=1),
        ),
    ]

    schedule = {job.id: estimate for job, estimate in estimate_queue_schedule(jobs, now)}

    assert schedule[1] == now
    assert schedule[2] == now + timedelta(hours=1)


def test_scheduled_publication_does_not_consume_regular_interval_slot():
    now = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)
    channel = SimpleNamespace(
        alias="art", publish_interval_seconds=600, next_publish_at=None,
    )
    jobs = [
        make_job(1, channel, now, queue_position=1),
        make_job(
            2, channel, now, status=JobStatus.SCHEDULED,
            scheduled_at=now + timedelta(minutes=5),
        ),
        make_job(3, channel, now, queue_position=2),
    ]

    schedule = {job.id: estimate for job, estimate in estimate_queue_schedule(jobs, now)}

    assert schedule == {
        1: now,
        2: now + timedelta(minutes=5),
        3: now + timedelta(minutes=10),
    }
    assert regular_queue_completion(jobs, now) == (2, now + timedelta(minutes=10))
