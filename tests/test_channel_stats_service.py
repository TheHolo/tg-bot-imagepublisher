from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db.models import Channel, ChannelMemberSnapshot
from app.db.session import create_database, create_schema
from app.services.channel_stats_service import (
    AUTOMATIC_SOURCE,
    MANUAL_SOURCE,
    SNAPSHOT_RETENTION_DAYS,
    SNAPSHOT_RETRY_SECONDS,
    ChannelStatsService,
)


class SubscriberBot:
    def __init__(self, result: int | Exception) -> None:
        self.result = result
        self.count_calls: list[str] = []
        self.messages: list[tuple[int, str]] = []

    async def get_chat_member_count(self, chat_id: str) -> int:
        self.count_calls.append(chat_id)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


async def create_context(tmp_path, name: str):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / name}")
    await create_schema(engine)
    async with sessions() as session, session.begin():
        channel = Channel(
            alias="artwork", telegram_chat_id="-1001", title="Artwork",
            is_default=True,
        )
        session.add(channel)
        await session.flush()
        channel_id = channel.id
    return engine, sessions, channel_id


async def load_channel(sessions, channel_id: int) -> Channel:
    async with sessions() as session:
        return await session.get(Channel, channel_id)


async def test_automatic_snapshot_is_taken_only_once_per_local_day(tmp_path):
    engine, sessions, _channel_id = await create_context(tmp_path, "daily.db")
    bot = SubscriberBot(1250)
    service = ChannelStatsService(
        bot=bot, sessions=sessions, admin_ids={1},
        timezone_name="Asia/Vladivostok",
    )
    now = datetime(2026, 7, 28, 2, tzinfo=UTC)

    assert await service.collect_daily(now=now) == 0
    assert await service.collect_daily(now=now + timedelta(hours=3)) == 0

    async with sessions() as session:
        snapshots = list((await session.scalars(
            select(ChannelMemberSnapshot)
        )).all())
    assert len(bot.count_calls) == 1
    assert len(snapshots) == 1
    assert snapshots[0].source == AUTOMATIC_SOURCE
    assert snapshots[0].snapshot_date.isoformat() == "2026-07-28"
    await engine.dispose()


async def test_opening_channel_always_records_a_manual_snapshot(tmp_path):
    engine, sessions, channel_id = await create_context(tmp_path, "manual.db")
    bot = SubscriberBot(100)
    service = ChannelStatsService(
        bot=bot, sessions=sessions, admin_ids={1}, timezone_name="UTC",
    )
    channel = await load_channel(sessions, channel_id)

    first = await service.capture_for_display(channel)
    bot.result = 103
    second = await service.capture_for_display(channel)

    async with sessions() as session:
        manual_count = await session.scalar(
            select(func.count(ChannelMemberSnapshot.id)).where(
                ChannelMemberSnapshot.source == MANUAL_SOURCE,
            )
        )
    assert first.count == 100
    assert second.count == 103
    assert manual_count == 2
    await engine.dispose()


async def test_failed_automatic_snapshot_alerts_once_and_remains_due(tmp_path):
    engine, sessions, channel_id = await create_context(tmp_path, "retry.db")
    bot = SubscriberBot(RuntimeError("channel is unavailable"))
    service = ChannelStatsService(
        bot=bot, sessions=sessions, admin_ids={10, 20}, timezone_name="UTC",
    )
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)

    assert await service.collect_daily(now=now) == 1
    assert await service.collect_daily(now=now + timedelta(minutes=5)) == 1
    assert len(bot.count_calls) == 2
    assert len(bot.messages) == 2
    assert {chat_id for chat_id, _ in bot.messages} == {10, 20}
    assert all("artwork" in text for _, text in bot.messages)
    assert all("RuntimeError: channel is unavailable" in text for _, text in bot.messages)
    assert all("через 5 минут" in text for _, text in bot.messages)
    assert SNAPSHOT_RETRY_SECONDS == 300

    bot.result = 150
    assert await service.collect_daily(now=now + timedelta(minutes=10)) == 0
    async with sessions() as session:
        snapshot = await session.scalar(select(ChannelMemberSnapshot))
    assert snapshot is not None and snapshot.channel_id == channel_id
    await engine.dispose()


async def test_summary_calculates_changes_and_cleanup_keeps_400_days(tmp_path):
    engine, sessions, channel_id = await create_context(tmp_path, "history.db")
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)
    async with sessions() as session, session.begin():
        session.add_all([
            ChannelMemberSnapshot(
                channel_id=channel_id, member_count=count, source=MANUAL_SOURCE,
                snapshot_date=None, captured_at=now - timedelta(days=days),
            )
            for days, count in [(401, 50), (30, 80), (7, 90), (1, 100), (0, 110)]
        ])

    service = ChannelStatsService(
        bot=SubscriberBot(110), sessions=sessions, admin_ids=set(),
        timezone_name="UTC",
    )
    assert await service.cleanup(now=now) == 1
    stats = await service.summary(channel_id, now=now)

    assert SNAPSHOT_RETENTION_DAYS == 400
    assert stats.count == 110
    assert stats.day is not None and stats.day.value == 10
    assert stats.day.percent == 10
    assert stats.week is not None and stats.week.value == 20
    assert round(stats.week.percent or 0, 2) == 22.22
    assert stats.month is not None and stats.month.value == 30
    assert stats.month.percent == 37.5
    await engine.dispose()
