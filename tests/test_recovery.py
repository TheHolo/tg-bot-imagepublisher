from sqlalchemy import inspect, select, text

from app.db.models import Channel, Job, User
from app.db.session import create_database, create_schema
from app.domain.enums import JobStatus
from app.services.job_service import JobService


async def test_existing_database_gets_channel_lease_column(tmp_path):
    engine, _ = create_database(f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}")
    await create_schema(engine)
    async with engine.begin() as connection:
        await connection.execute(text("DROP INDEX ix_channels_active_job_id"))
        await connection.execute(text("ALTER TABLE channels DROP COLUMN active_job_id"))

    await create_schema(engine)

    async with engine.begin() as connection:
        columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"] for column in inspect(sync_connection).get_columns("channels")
            }
        )
    assert "active_job_id" in columns
    await engine.dispose()


async def test_startup_recovery_handles_fresh_jobs_and_releases_channel_leases(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    await create_schema(engine)
    async with sessions() as session, session.begin():
        user = User(telegram_user_id=1)
        channels = [
            Channel(alias=f"channel-{index}", telegram_chat_id=f"-100{index}", title=f"Channel {index}")
            for index in range(4)
        ]
        session.add_all([user, *channels])
        await session.flush()
        jobs = [
            Job(
                created_by_user_id=user.id, provider="direct", source_id=f"job-{index}",
                source_url=f"https://x/{index}.jpg", normalized_url=f"https://x/{index}.jpg",
                target_channel_id=channels[index].id, status=status,
                post_data={}, user_tags=[], source_tags=[], cancel_requested=cancel_requested,
            )
            for index, (status, cancel_requested) in enumerate(
                [
                    (JobStatus.DOWNLOADING, False),
                    (JobStatus.PROCESSING, False),
                    (JobStatus.PUBLISHING, False),
                    (JobStatus.DOWNLOADING, True),
                ]
            )
        ]
        session.add_all(jobs)
        await session.flush()
        for channel, job in zip(channels, jobs, strict=True):
            channel.active_job_id = job.id
        job_ids = [job.id for job in jobs]

    recovered = await JobService(sessions).recover()

    async with sessions() as session:
        stored_jobs = list(
            (await session.scalars(select(Job).where(Job.id.in_(job_ids)).order_by(Job.id))).all()
        )
        stored_channels = list(
            (await session.scalars(select(Channel).order_by(Channel.id))).all()
        )

    assert recovered == 4
    assert [job.status for job in stored_jobs] == [
        JobStatus.QUEUED,
        JobStatus.QUEUED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    ]
    assert stored_jobs[0].error_code == "recovered_after_restart"
    assert stored_jobs[1].error_code == "recovered_after_restart"
    assert stored_jobs[2].error_code == "uncertain_publish"
    assert stored_jobs[2].finished_at is not None
    assert stored_jobs[3].finished_at is not None
    assert all(channel.active_job_id is None for channel in stored_channels)
    await engine.dispose()


async def test_timed_recovery_does_not_take_over_fresh_live_job(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'watchdog.db'}")
    await create_schema(engine)
    async with sessions() as session, session.begin():
        user = User(telegram_user_id=1)
        channel = Channel(alias="main", telegram_chat_id="-1001", title="Main")
        session.add_all([user, channel])
        await session.flush()
        job = Job(
            created_by_user_id=user.id, provider="direct", source_id="fresh",
            source_url="https://x/fresh.jpg", normalized_url="https://x/fresh.jpg",
            target_channel_id=channel.id, status=JobStatus.DOWNLOADING,
            post_data={}, user_tags=[], source_tags=[],
        )
        session.add(job)
        await session.flush()
        channel.active_job_id = job.id
        job_id = job.id

    assert await JobService(sessions).recover(stale_minutes=15) == 0

    async with sessions() as session:
        stored = await session.get(Job, job_id)
        stored_channel = await session.get(Channel, channel.id)
    assert stored.status == JobStatus.DOWNLOADING
    assert stored_channel.active_job_id == job_id
    await engine.dispose()
