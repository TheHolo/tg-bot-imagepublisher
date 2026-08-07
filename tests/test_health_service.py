from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.db.models import Channel, Job, Publication, User, utcnow
from app.db.session import create_database, create_schema
from app.domain.enums import JobStatus
from app.queue.worker import WorkerSnapshot
from app.services.health_service import (
    HealthLevel,
    HealthService,
    format_size,
    render_health_report,
)


class FakeWorkers:
    count = 2

    def snapshot(self) -> list[WorkerSnapshot]:
        return [
            WorkerSnapshot(
                index=0, alive=True, job_id=99, channel_alias="artwork",
                stage="downloading 1/2", busy_seconds=42, heartbeat_age_seconds=1,
            ),
            WorkerSnapshot(
                index=1, alive=True, job_id=None, channel_alias=None,
                stage=None, busy_seconds=None, heartbeat_age_seconds=2,
            ),
        ]


async def build_health_service(tmp_path: Path, *, with_provider: bool = False):
    database_path = tmp_path / "health.db"
    engine, sessions = create_database(f"sqlite+aiosqlite:///{database_path}")
    await create_schema(engine)
    now = utcnow()
    async with sessions() as session, session.begin():
        user = User(telegram_user_id=1)
        artwork = Channel(
            alias="artwork", telegram_chat_id="-1001", title="Artwork",
            is_enabled=True, is_default=True,
        )
        archive = Channel(
            alias="archive", telegram_chat_id="-1002", title="Archive",
            is_enabled=True,
        )
        session.add_all([user, artwork, archive])
        await session.flush()
        published = Job(
            created_by_user_id=user.id, provider="direct", source_id="published",
            source_url="https://x/published.jpg", normalized_url="https://x/published.jpg",
            target_channel_id=artwork.id, status=JobStatus.COMPLETED,
            post_data={}, user_tags=[], source_tags=[], finished_at=now - timedelta(minutes=7),
        )
        queued = Job(
            created_by_user_id=user.id, provider="direct", source_id="queued",
            source_url="https://x/queued.jpg", normalized_url="https://x/queued.jpg",
            target_channel_id=archive.id, status=JobStatus.QUEUED,
            post_data={}, user_tags=[], source_tags=[], created_at=now - timedelta(minutes=42),
        )
        failed = Job(
            created_by_user_id=user.id, provider="direct", source_id="failed",
            source_url="https://x/failed.jpg", normalized_url="https://x/failed.jpg",
            target_channel_id=artwork.id, status=JobStatus.FAILED,
            post_data={}, user_tags=[], source_tags=[], error_code="download_error",
            finished_at=now,
        )
        session.add_all([published, queued, failed])
        await session.flush()
        session.add(Publication(
            job_id=published.id, channel_id=artwork.id, telegram_chat_id=artwork.telegram_chat_id,
            telegram_message_ids=[10], published_at=now - timedelta(minutes=7), caption="caption",
        ))

    bot = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(id=42)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(
            status="administrator", can_post_messages=True,
        )),
    )
    provider = SimpleNamespace(
        name="pixiv", healthcheck_url="https://www.pixiv.net/",
        healthcheck=AsyncMock(return_value=200),
    )
    registry = SimpleNamespace(providers=(provider,) if with_provider else ())
    storage = tmp_path / "storage"
    storage.mkdir()
    service = HealthService(
        bot=bot, sessions=sessions, workers=FakeWorkers(), storage=storage,
        registry=registry, database_url=f"sqlite+aiosqlite:///{database_path}",
    )
    return service, engine, bot, provider, storage


async def test_default_health_reports_real_runtime_state(tmp_path):
    service, engine, bot, _, _ = await build_health_service(tmp_path)

    report = await service.check()
    rendered = render_health_report(report)

    assert report.healthy is True
    assert [line.label for line in report.lines] == [
        "Database", "Workers", "Queue", "Publications", "Failures",
        "Channels", "Storage", "Telegram",
    ]
    assert "2/2 active · занято 1" in rendered
    assert "worker-0 · #99 downloading 1/2 · artwork · 42s" in rendered
    assert "1 queued · oldest 42m" in rendered
    assert "last success 7m ago · artwork" in rendered
    assert "2 enabled · leases OK" in rendered
    assert "API" in rendered
    bot.get_me.assert_awaited_once()
    await engine.dispose()


async def test_full_health_checks_size_channels_storage_and_providers(tmp_path):
    service, engine, bot, provider, storage = await build_health_service(
        tmp_path, with_provider=True,
    )

    report = await service.check(full=True)
    rendered = render_health_report(report)

    assert report.healthy is True
    assert "размер" in rendered
    assert "artwork · 7m ago" in rendered
    assert "archive · never" in rendered
    assert "artwork · can post" in rendered
    assert "archive · can post" in rendered
    assert "write test OK" in rendered
    assert "Providers" in rendered
    assert "pixiv · HTTP 200" in rendered
    assert not list(storage.glob(".health-*"))
    assert bot.get_chat_member.await_count == 2
    provider.healthcheck.assert_awaited_once()
    await engine.dispose()


async def test_dead_worker_marks_report_unhealthy(tmp_path):
    service, engine, _, _, _ = await build_health_service(tmp_path)
    service.workers = SimpleNamespace(
        count=2,
        snapshot=lambda: [
            WorkerSnapshot(0, True, None, None, None, None, 1),
            WorkerSnapshot(1, False, None, None, None, None, None),
        ],
    )

    report = await service.check()
    workers = next(line for line in report.lines if line.label == "Workers")

    assert report.healthy is False
    assert workers.level == HealthLevel.ERROR
    assert workers.summary.startswith("1/2 active")
    await engine.dispose()


async def test_full_health_provider_failure_is_warning(tmp_path):
    service, engine, _, provider, _ = await build_health_service(tmp_path, with_provider=True)
    provider.healthcheck.side_effect = TimeoutError

    report = await service.check(full=True)
    providers = next(line for line in report.lines if line.label == "Providers")

    assert report.healthy is True
    assert providers.level == HealthLevel.WARNING
    assert "0/1 available" in providers.summary
    await engine.dispose()


async def test_full_health_missing_channel_permission_is_error(tmp_path):
    service, engine, bot, _, _ = await build_health_service(tmp_path)
    bot.get_chat_member.return_value = SimpleNamespace(
        status="administrator", can_post_messages=False,
    )

    report = await service.check(full=True)
    channels = next(line for line in report.lines if line.label == "Channels")

    assert report.healthy is False
    assert channels.level == HealthLevel.ERROR
    assert "rights errors: 2" in channels.summary
    await engine.dispose()


def test_database_size_switches_to_gigabytes_at_1024_megabytes():
    assert format_size(1024 ** 3) == "1.0 GB"
    assert format_size(512 * 1024 ** 2) == "512.0 MB"
