import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Channel, ChannelMemberSnapshot

logger = logging.getLogger(__name__)

AUTOMATIC_SOURCE = "automatic"
MANUAL_SOURCE = "manual"
SNAPSHOT_RETRY_SECONDS = 5 * 60
SNAPSHOT_RETENTION_DAYS = 400
TELEGRAM_REQUEST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class SubscriberChange:
    value: int
    percent: float | None


@dataclass(frozen=True)
class ChannelSubscriberStats:
    count: int | None
    captured_at: datetime | None
    day: SubscriberChange | None
    week: SubscriberChange | None
    month: SubscriberChange | None
    error: str | None = None


class ChannelStatsService:
    def __init__(
        self, *, bot: Bot, sessions: async_sessionmaker[AsyncSession],
        admin_ids: set[int], timezone_name: str,
    ) -> None:
        self.bot = bot
        self.sessions = sessions
        self.admin_ids = set(admin_ids)
        self.timezone = ZoneInfo(timezone_name)
        self._task: asyncio.Task[None] | None = None
        self._alerted_failures: set[tuple[date, int]] = set()
        self._last_cleanup_date: date | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run(), name="channel-subscriber-snapshots",
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def capture_for_display(self, channel: Channel) -> ChannelSubscriberStats:
        error: str | None = None
        try:
            await self._capture(channel, source=MANUAL_SOURCE)
        except Exception as exc:
            error = describe_snapshot_error(exc)
            logger.warning(
                "manual_channel_snapshot_failed channel=%s", channel.alias,
                exc_info=True,
            )
        return await self.summary(channel.id, error=error)

    async def summary(
        self, channel_id: int, *, error: str | None = None,
        now: datetime | None = None,
    ) -> ChannelSubscriberStats:
        current_time = ensure_utc(now or datetime.now(UTC))
        try:
            async with self.sessions() as session:
                snapshots = list((await session.scalars(
                    select(ChannelMemberSnapshot)
                    .where(ChannelMemberSnapshot.channel_id == channel_id)
                    .order_by(ChannelMemberSnapshot.captured_at.desc())
                )).all())
        except Exception as exc:  # noqa: BLE001 - keep the channel screen available on DB errors
            summary_error = describe_snapshot_error(exc)
            return ChannelSubscriberStats(
                count=None, captured_at=None, day=None, week=None, month=None,
                error=error or summary_error,
            )

        if not snapshots:
            return ChannelSubscriberStats(
                count=None, captured_at=None, day=None, week=None, month=None,
                error=error,
            )

        latest = snapshots[0]
        return ChannelSubscriberStats(
            count=latest.member_count,
            captured_at=ensure_utc(latest.captured_at),
            day=self._change_since(snapshots, latest.member_count, current_time, days=1),
            week=self._change_since(snapshots, latest.member_count, current_time, days=7),
            month=self._change_since(snapshots, latest.member_count, current_time, days=30),
            error=error,
        )

    async def collect_daily(self, *, now: datetime | None = None) -> int:
        current_time = ensure_utc(now or datetime.now(UTC))
        snapshot_date = current_time.astimezone(self.timezone).date()
        if self._last_cleanup_date != snapshot_date:
            await self.cleanup(now=current_time)
            self._last_cleanup_date = snapshot_date
            self._alerted_failures = {
                key for key in self._alerted_failures if key[0] >= snapshot_date
            }

        async with self.sessions() as session:
            channels = list((await session.scalars(
                select(Channel).order_by(Channel.id)
            )).all())
            captured_channel_ids = set((await session.scalars(
                select(ChannelMemberSnapshot.channel_id).where(
                    ChannelMemberSnapshot.source == AUTOMATIC_SOURCE,
                    ChannelMemberSnapshot.snapshot_date == snapshot_date,
                )
            )).all())

        failures = 0
        for channel in channels:
            if channel.id in captured_channel_ids:
                continue
            try:
                await self._capture(
                    channel, source=AUTOMATIC_SOURCE,
                    captured_at=current_time, snapshot_date=snapshot_date,
                )
                self._alerted_failures.discard((snapshot_date, channel.id))
            except Exception as exc:
                failures += 1
                logger.warning(
                    "automatic_channel_snapshot_failed channel=%s", channel.alias,
                    exc_info=True,
                )
                await self._notify_failure(channel, snapshot_date, exc)
        return failures

    async def cleanup(self, *, now: datetime | None = None) -> int:
        current_time = ensure_utc(now or datetime.now(UTC))
        cutoff = current_time - timedelta(days=SNAPSHOT_RETENTION_DAYS)
        async with self.sessions() as session, session.begin():
            result = await session.execute(
                delete(ChannelMemberSnapshot)
                .where(ChannelMemberSnapshot.captured_at < cutoff)
            )
            return int(result.rowcount or 0)

    async def _capture(
        self, channel: Channel, *, source: str,
        captured_at: datetime | None = None, snapshot_date: date | None = None,
    ) -> ChannelMemberSnapshot:
        captured_at = ensure_utc(captured_at or datetime.now(UTC))
        count = await asyncio.wait_for(
            self.bot.get_chat_member_count(channel.telegram_chat_id),
            timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS,
        )
        count = int(count)
        if count < 0:
            raise ValueError("Telegram returned a negative member count")

        async with self.sessions() as session, session.begin():
            if source == AUTOMATIC_SOURCE:
                existing = await session.scalar(
                    select(ChannelMemberSnapshot).where(
                        ChannelMemberSnapshot.channel_id == channel.id,
                        ChannelMemberSnapshot.source == AUTOMATIC_SOURCE,
                        ChannelMemberSnapshot.snapshot_date == snapshot_date,
                    )
                )
                if existing is not None:
                    return existing
            snapshot = ChannelMemberSnapshot(
                channel_id=channel.id,
                member_count=count,
                source=source,
                snapshot_date=snapshot_date,
                captured_at=captured_at,
            )
            session.add(snapshot)
            await session.flush()
            return snapshot

    async def _notify_failure(
        self, channel: Channel, snapshot_date: date, error: Exception,
    ) -> None:
        key = (snapshot_date, channel.id)
        if key in self._alerted_failures:
            return
        reason = describe_snapshot_error(error)
        text = (
            "⚠️ Не удалось сохранить ежедневный снимок подписчиков.\n"
            f"Канал: {channel.title} ({channel.alias})\n"
            f"Причина: {reason}\n"
            "Следующая попытка — через 5 минут."
        )
        delivered = False
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
                delivered = True
            except Exception:
                logger.warning(
                    "channel_snapshot_alert_failed admin_id=%s channel=%s",
                    admin_id, channel.alias, exc_info=True,
                )
        if delivered:
            self._alerted_failures.add(key)

    async def _run(self) -> None:
        while True:
            failures = 1
            try:
                failures = await self.collect_daily()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("channel_snapshot_cycle_failed")

            delay = (
                SNAPSHOT_RETRY_SECONDS if failures
                else seconds_until_next_local_day(datetime.now(UTC), self.timezone)
            )
            await asyncio.sleep(delay)

    @staticmethod
    def _change_since(
        snapshots: list[ChannelMemberSnapshot], current_count: int,
        now: datetime, *, days: int,
    ) -> SubscriberChange | None:
        cutoff = now - timedelta(days=days)
        baseline = next(
            (
                snapshot for snapshot in snapshots
                if ensure_utc(snapshot.captured_at) <= cutoff
            ),
            None,
        )
        if baseline is None:
            return None
        value = current_count - baseline.member_count
        percent = (
            value / baseline.member_count * 100
            if baseline.member_count else None
        )
        return SubscriberChange(value=value, percent=percent)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def seconds_until_next_local_day(now: datetime, timezone: ZoneInfo) -> float:
    local_now = ensure_utc(now).astimezone(timezone)
    next_date = local_now.date() + timedelta(days=1)
    next_midnight = datetime.combine(next_date, time.min, tzinfo=timezone)
    return max(1.0, (next_midnight.astimezone(UTC) - ensure_utc(now)).total_seconds())


def describe_snapshot_error(error: Exception) -> str:
    details = " ".join(str(error).split())
    return f"{type(error).__name__}: {details}" if details else type(error).__name__
