import asyncio
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from html import escape
from pathlib import Path

from aiogram import Bot
from sqlalchemy import func, or_, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Channel, Job, Publication
from app.domain.enums import JobStatus
from app.providers.registry import ProviderRegistry
from app.queue.worker import WorkerPool


class HealthLevel(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class HealthLine:
    label: str
    level: HealthLevel
    summary: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class HealthReport:
    healthy: bool
    uptime_seconds: float
    elapsed_ms: int
    lines: tuple[HealthLine, ...]


@dataclass(frozen=True)
class ChannelTarget:
    id: int
    alias: str
    chat_id: str


@dataclass(frozen=True)
class ChannelPublication:
    alias: str
    published_at: datetime | None


@dataclass(frozen=True)
class DatabaseSnapshot:
    latency_ms: int
    size_bytes: int | None
    queued_count: int
    oldest_queued: datetime | None
    stalled_count: int
    failures_24h: int
    uncertain_24h: int
    enabled_channels: tuple[ChannelTarget, ...]
    default_channels: int
    lease_errors: int
    last_publication: ChannelPublication | None
    publications_by_channel: tuple[ChannelPublication, ...]


class HealthService:
    def __init__(
        self, *, bot: Bot, sessions: async_sessionmaker[AsyncSession], workers: WorkerPool,
        storage: Path, registry: ProviderRegistry, database_url: str,
    ) -> None:
        self.bot = bot
        self.sessions = sessions
        self.workers = workers
        self.storage = storage
        self.registry = registry
        self.database_url = database_url
        self.started_at = time.monotonic()

    async def check(self, *, full: bool = False) -> HealthReport:
        started = time.perf_counter()
        providers_task = asyncio.create_task(self._providers_line()) if full else None
        database_result, storage_line, telegram_result = await asyncio.gather(
            self._database_snapshot(full=full),
            self._storage_line(full=full),
            self._telegram_line(),
            return_exceptions=True,
        )

        lines: list[HealthLine] = []
        database: DatabaseSnapshot | None = None
        database_lines: list[HealthLine]
        unavailable_channels: HealthLine | None = None
        if isinstance(database_result, BaseException):
            reason = type(database_result).__name__
            lines.append(HealthLine("Database", HealthLevel.ERROR, f"недоступна · {reason}"))
            database_lines = [
                HealthLine("Queue", HealthLevel.ERROR, "данные недоступны"),
                HealthLine("Publications", HealthLevel.ERROR, "данные недоступны"),
                HealthLine("Failures", HealthLevel.ERROR, "данные недоступны"),
            ]
            unavailable_channels = HealthLine("Channels", HealthLevel.ERROR, "данные недоступны")
        else:
            database = database_result
            lines.append(self._database_line(database, full=full))
            database_lines = [
                self._queue_line(database),
                self._publications_line(database, full=full),
                self._failures_line(database),
            ]

        lines.append(self._workers_line())
        lines.extend(database_lines)

        telegram_line: HealthLine
        bot_id: int | None
        if isinstance(telegram_result, BaseException):
            telegram_line = HealthLine(
                "Telegram", HealthLevel.ERROR,
                f"API недоступен · {type(telegram_result).__name__}",
            )
            bot_id = None
        else:
            telegram_line, bot_id = telegram_result

        if database is not None:
            lines.append(await self._channels_line(database, bot_id=bot_id, full=full))
        elif unavailable_channels is not None:
            lines.append(unavailable_channels)

        if isinstance(storage_line, BaseException):
            lines.append(HealthLine(
                "Storage", HealthLevel.ERROR,
                f"недоступен · {type(storage_line).__name__}",
            ))
        else:
            lines.append(storage_line)

        lines.append(telegram_line)
        if providers_task is not None:
            lines.append(await providers_task)

        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return HealthReport(
            healthy=not any(line.level == HealthLevel.ERROR for line in lines),
            uptime_seconds=time.monotonic() - self.started_at,
            elapsed_ms=elapsed_ms,
            lines=tuple(lines),
        )

    async def _database_snapshot(self, *, full: bool) -> DatabaseSnapshot:
        async with self.sessions() as session:
            latency_started = time.perf_counter()
            await session.execute(text("SELECT 1"))
            latency_ms = round((time.perf_counter() - latency_started) * 1000)

            queued_count, oldest_queued = (
                await session.execute(
                    select(func.count(Job.id), func.min(Job.created_at))
                    .where(Job.status == JobStatus.QUEUED)
                )
            ).one()
            cutoff = datetime.now(UTC) - timedelta(hours=24)
            failures_24h, uncertain_24h = (
                await session.execute(
                    select(
                        func.count(Job.id),
                        func.count(Job.id).filter(Job.error_code == "uncertain_publish"),
                    )
                    .where(Job.status == JobStatus.FAILED, Job.updated_at >= cutoff)
                )
            ).one()
            stalled_cutoff = datetime.now(UTC) - timedelta(minutes=15)
            stalled_count = await session.scalar(
                select(func.count(Job.id)).where(
                    Job.status.in_({JobStatus.DOWNLOADING, JobStatus.PROCESSING, JobStatus.PUBLISHING}),
                    Job.updated_at < stalled_cutoff,
                )
            )

            all_channels = list((await session.scalars(
                select(Channel).order_by(Channel.alias)
            )).all())
            channels = [channel for channel in all_channels if channel.is_enabled]
            lease_errors = await self._lease_errors(session, all_channels)

            publication_row = (
                await session.execute(
                    select(Channel.alias, Publication.published_at)
                    .join(Channel, Publication.channel_id == Channel.id)
                    .order_by(Publication.published_at.desc())
                    .limit(1)
                )
            ).one_or_none()
            last_publication = (
                ChannelPublication(str(publication_row.alias), publication_row.published_at)
                if publication_row else None
            )
            per_channel_rows = (
                await session.execute(
                    select(Channel.alias, func.max(Publication.published_at).label("published_at"))
                    .outerjoin(Publication, Publication.channel_id == Channel.id)
                    .where(Channel.is_enabled.is_(True))
                    .group_by(Channel.id, Channel.alias)
                    .order_by(Channel.alias)
                )
            ).all()
            publications_by_channel = tuple(
                ChannelPublication(str(row.alias), row.published_at) for row in per_channel_rows
            )
            size_bytes = await self._database_size(session) if full else None

        return DatabaseSnapshot(
            latency_ms=latency_ms,
            size_bytes=size_bytes,
            queued_count=int(queued_count or 0),
            oldest_queued=oldest_queued,
            stalled_count=int(stalled_count or 0),
            failures_24h=int(failures_24h or 0),
            uncertain_24h=int(uncertain_24h or 0),
            enabled_channels=tuple(
                ChannelTarget(channel.id, channel.alias, channel.telegram_chat_id)
                for channel in channels
            ),
            default_channels=sum(channel.is_default for channel in channels),
            lease_errors=lease_errors,
            last_publication=last_publication,
            publications_by_channel=publications_by_channel,
        )

    async def _lease_errors(self, session: AsyncSession, channels: list[Channel]) -> int:
        active_statuses = {JobStatus.DOWNLOADING, JobStatus.PROCESSING, JobStatus.PUBLISHING}
        leased_ids = {channel.active_job_id for channel in channels if channel.active_job_id is not None}
        leased_jobs = {
            job.id: job for job in (
                await session.scalars(select(Job).where(Job.id.in_(leased_ids)))
            ).all()
        } if leased_ids else {}

        issues: set[tuple[str, int]] = set()
        for channel in channels:
            if channel.active_job_id is None:
                continue
            job = leased_jobs.get(channel.active_job_id)
            if not job or job.target_channel_id != channel.id or job.status not in active_statuses:
                issues.add(("channel", channel.id))

        active_jobs = (
            await session.execute(
                select(Job.id, Job.target_channel_id, Channel.active_job_id)
                .join(Channel, Job.target_channel_id == Channel.id)
                .where(
                    Job.status.in_(active_statuses),
                    or_(Channel.active_job_id.is_(None), Channel.active_job_id != Job.id),
                )
            )
        ).all()
        issues.update(("job", int(row.id)) for row in active_jobs)
        return len(issues)

    async def _database_size(self, session: AsyncSession) -> int | None:
        url = make_url(self.database_url)
        backend = url.get_backend_name()
        if backend == "sqlite":
            if not url.database or url.database == ":memory:":
                return None
            path = Path(url.database)
            if not path.is_absolute():
                path = Path.cwd() / path
            return path.stat().st_size if path.exists() else 0
        if backend == "postgresql":
            value = await session.scalar(text("SELECT pg_database_size(current_database())"))
            return int(value) if value is not None else None
        return None

    def _database_line(self, database: DatabaseSnapshot, *, full: bool) -> HealthLine:
        summary = f"OK · {database.latency_ms} мс"
        if full and database.size_bytes is not None:
            summary += f" · размер {format_size(database.size_bytes)}"
        return HealthLine("Database", HealthLevel.OK, summary)

    def _workers_line(self) -> HealthLine:
        snapshots = self.workers.snapshot()
        active = sum(snapshot.alive for snapshot in snapshots)
        busy = [snapshot for snapshot in snapshots if snapshot.alive and snapshot.job_id is not None]
        stale = [
            snapshot for snapshot in busy
            if snapshot.heartbeat_age_seconds is not None and snapshot.heartbeat_age_seconds > 120
        ]
        level = HealthLevel.ERROR if active != self.workers.count else (
            HealthLevel.WARNING if stale else HealthLevel.OK
        )
        details = tuple(
            f"worker-{snapshot.index} · #{snapshot.job_id} {snapshot.stage or 'working'}"
            f" · {snapshot.channel_alias or 'unknown'} · {format_duration(snapshot.busy_seconds or 0)}"
            for snapshot in busy
        )
        summary = f"{active}/{self.workers.count} active · занято {len(busy)}"
        if stale:
            summary += f" · stale heartbeat: {len(stale)}"
        return HealthLine(
            "Workers", level,
            summary,
            details,
        )

    def _queue_line(self, database: DatabaseSnapshot) -> HealthLine:
        age = age_seconds(database.oldest_queued)
        summary = f"{database.queued_count} queued"
        if age is not None:
            summary += f" · oldest {format_duration(age)}"
        if database.stalled_count:
            summary += f" · stalled {database.stalled_count}"
        level = HealthLevel.ERROR if database.stalled_count else (
            HealthLevel.WARNING if database.queued_count else HealthLevel.OK
        )
        return HealthLine("Queue", level, summary)

    def _publications_line(self, database: DatabaseSnapshot, *, full: bool) -> HealthLine:
        publication = database.last_publication
        if publication is None:
            summary = "успешных публикаций ещё нет"
            level = HealthLevel.WARNING
        else:
            summary = (
                f"last success {format_age(publication.published_at)} · {publication.alias}"
            )
            level = HealthLevel.OK
        details: tuple[str, ...] = ()
        if full:
            details = tuple(
                f"{item.alias} · {format_age(item.published_at)}"
                for item in database.publications_by_channel
            )
        return HealthLine("Publications", level, summary, details)

    def _failures_line(self, database: DatabaseSnapshot) -> HealthLine:
        level = HealthLevel.WARNING if database.failures_24h else HealthLevel.OK
        return HealthLine(
            "Failures", level,
            f"{database.failures_24h} за 24h · uncertain: {database.uncertain_24h}",
        )

    async def _channels_line(
        self, database: DatabaseSnapshot, *, bot_id: int | None, full: bool,
    ) -> HealthLine:
        count = len(database.enabled_channels)
        level = HealthLevel.OK
        if count == 0 or database.default_channels != 1 or database.lease_errors:
            level = HealthLevel.ERROR
        summary = f"{count} enabled · " + (
            "leases OK" if not database.lease_errors else f"lease errors: {database.lease_errors}"
        )
        if database.default_channels != 1:
            summary += f" · default channels: {database.default_channels}"
        details: tuple[str, ...] = ()
        if full:
            if bot_id is None:
                level = HealthLevel.ERROR
                details = ("права не проверены · Telegram API недоступен",)
            else:
                permission_results = await asyncio.gather(*[
                    self._channel_permission(channel, bot_id)
                    for channel in database.enabled_channels
                ])
                failed = sum(not result[0] for result in permission_results)
                if failed:
                    level = HealthLevel.ERROR
                    summary += f" · rights errors: {failed}"
                details = tuple(result[1] for result in permission_results)
        return HealthLine("Channels", level, summary, details)

    async def _channel_permission(self, channel: ChannelTarget, bot_id: int) -> tuple[bool, str]:
        started = time.perf_counter()
        try:
            member = await asyncio.wait_for(
                self.bot.get_chat_member(channel.chat_id, bot_id), timeout=5,
            )
            status_value = getattr(member.status, "value", str(member.status))
            can_post = status_value == "creator" or (
                status_value == "administrator" and bool(getattr(member, "can_post_messages", False))
            )
            latency = round((time.perf_counter() - started) * 1000)
            return can_post, (
                f"{channel.alias} · {'can post' if can_post else 'нет права публикации'} · {latency} мс"
            )
        except Exception as error:  # noqa: BLE001 - report any Telegram probe failure
            return False, f"{channel.alias} · ошибка прав · {type(error).__name__}"

    async def _storage_line(self, *, full: bool) -> HealthLine:
        def inspect_storage() -> HealthLine:
            if not self.storage.is_dir():
                raise FileNotFoundError(self.storage)
            writable = os.access(self.storage, os.W_OK)
            if not writable:
                raise PermissionError(self.storage)
            write_tested = False
            if full:
                temporary_path: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        prefix=".health-", dir=self.storage, delete=False,
                    ) as temporary:
                        temporary.write(b"health")
                        temporary.flush()
                        os.fsync(temporary.fileno())
                        temporary_path = Path(temporary.name)
                    write_tested = True
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
            usage = shutil.disk_usage(self.storage)
            free_ratio = usage.free / usage.total if usage.total else 0
            level = HealthLevel.WARNING if usage.free < 1024 ** 3 or free_ratio < 0.05 else HealthLevel.OK
            access = "write test OK" if write_tested else "writable"
            return HealthLine("Storage", level, f"{access} · свободно {format_size(usage.free)}")

        return await asyncio.to_thread(inspect_storage)

    async def _telegram_line(self) -> tuple[HealthLine, int]:
        started = time.perf_counter()
        me = await asyncio.wait_for(self.bot.get_me(), timeout=5)
        latency = round((time.perf_counter() - started) * 1000)
        return HealthLine("Telegram", HealthLevel.OK, f"API {latency} мс"), me.id

    async def _providers_line(self) -> HealthLine:
        providers = [provider for provider in self.registry.providers if provider.healthcheck_url]
        if not providers:
            return HealthLine("Providers", HealthLevel.OK, "внешних probes нет")
        results = await asyncio.gather(*[self._provider_probe(provider) for provider in providers])
        failed = sum(not result[0] for result in results)
        level = HealthLevel.WARNING if failed else HealthLevel.OK
        summary = f"{len(results) - failed}/{len(results)} available"
        return HealthLine("Providers", level, summary, tuple(result[1] for result in results))

    async def _provider_probe(self, provider) -> tuple[bool, str]:
        started = time.perf_counter()
        try:
            status = await asyncio.wait_for(provider.healthcheck(), timeout=5)
            latency = round((time.perf_counter() - started) * 1000)
            return True, f"{provider.name} · HTTP {status} · {latency} мс"
        except Exception as error:  # noqa: BLE001 - provider probes must stay isolated
            return False, f"{provider.name} · недоступен · {type(error).__name__}"


def render_health_report(report: HealthReport) -> str:
    status_icon = "🟢" if report.healthy else "🔴"
    status_text = "Bot healthy" if report.healthy else "Bot unhealthy"
    header = (
        f"{status_icon} <b>{status_text}</b> · uptime {format_duration(report.uptime_seconds)}"
        f" · проверка {report.elapsed_ms} мс"
    )
    body: list[str] = []
    icons = {
        HealthLevel.OK: "🟢",
        HealthLevel.WARNING: "🟡",
        HealthLevel.ERROR: "🔴",
    }
    for line in report.lines:
        body.append(f"{line.label:<12} {icons[line.level]} {line.summary}")
        for index, detail in enumerate(line.details):
            branch = "└" if index == len(line.details) - 1 else "├"
            body.append(f"  {branch} {detail}")
    return f"{header}\n\n<pre>{escape(chr(10).join(body))}</pre>"


def age_seconds(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - value).total_seconds())


def format_age(value: datetime | None) -> str:
    age = age_seconds(value)
    return "never" if age is None else f"{format_duration(age)} ago"


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def format_size(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"
