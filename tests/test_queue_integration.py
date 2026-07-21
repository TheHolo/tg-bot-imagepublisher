import asyncio

from app.db.models import Channel, Job, User
from app.db.session import create_database, create_schema
from app.domain.enums import JobStatus
from app.services.job_service import JobService


async def test_claim_is_atomic(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}")
    await create_schema(engine)
    async with sessions() as session, session.begin():
        user = User(telegram_user_id=1)
        channel = Channel(alias="main", telegram_chat_id="-1001", title="Main", is_default=True)
        session.add_all([user, channel])
        await session.flush()
        session.add(Job(
            created_by_user_id=user.id, provider="direct", source_id="x", source_url="https://x/a.jpg",
            normalized_url="https://x/a.jpg", target_channel_id=channel.id, status=JobStatus.QUEUED,
            post_data={}, user_tags=[], source_tags=[],
        ))
    jobs = JobService(sessions)
    first, second = await asyncio.gather(jobs.claim_next(), jobs.claim_next())
    assert sum(item is not None for item in (first, second)) == 1
    claimed = first or second
    assert claimed.status == JobStatus.DOWNLOADING
    assert claimed.attempts == 1
    await engine.dispose()
