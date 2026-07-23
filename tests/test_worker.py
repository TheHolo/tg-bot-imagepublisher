import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.domain.enums import JobStatus
from app.domain.exceptions import DownloadError, UncertainPublishError
from app.domain.models import DownloadedMedia, MediaItem, PreparedMedia, SourcePost
from app.queue import worker as worker_module
from app.queue.worker import WorkerPool
from app.services.job_service import serialize_post


def make_pool(jobs) -> WorkerPool:
    return WorkerPool(
        bot=SimpleNamespace(), sessions=Mock(), jobs=jobs,
        downloader=SimpleNamespace(download=AsyncMock()),
        media=SimpleNamespace(prepare=AsyncMock()),
        captions=SimpleNamespace(build=Mock(return_value="caption")),
        publisher=SimpleNamespace(publish=AsyncMock()),
        count=1, wakeup=asyncio.Event(), delete_after_publish=True,
        storage=Path("storage"), auto_add_source_tags=True,
        max_tags=20, max_tag_length=64,
        translator=SimpleNamespace(enrich_title=AsyncMock()),
    )


async def test_worker_does_not_retry_uncertain_telegram_result():
    job = SimpleNamespace(id=7, provider="direct", attempts=1, max_attempts=3)
    jobs = SimpleNamespace(claim_next=AsyncMock(), fail=AsyncMock())
    jobs.claim_next.return_value = job
    pool = make_pool(jobs)
    error = UncertainPublishError("check channel")
    pool._process = AsyncMock(side_effect=error)
    pool._notify = AsyncMock()

    async def stop_after_failure(*args):
        pool.stopping = True

    jobs.fail.side_effect = stop_after_failure

    await pool._run(0)

    jobs.fail.assert_awaited_once_with(job.id, error, False)
    pool._process.assert_awaited_once_with(job)


async def test_retryable_download_failure_does_not_sleep_and_block_worker(monkeypatch):
    job = SimpleNamespace(id=8, provider="direct", attempts=1, max_attempts=3)
    jobs = SimpleNamespace(claim_next=AsyncMock(return_value=job), fail=AsyncMock())
    pool = make_pool(jobs)
    pool._process = AsyncMock(side_effect=DownloadError("temporary"))
    pool._notify = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr(worker_module.asyncio, "sleep", sleep)

    async def stop_after_failure(*args):
        pool.stopping = True

    jobs.fail.side_effect = stop_after_failure

    await pool._run(0)

    assert jobs.fail.await_args.args[2] is True
    sleep.assert_not_awaited()
    assert pool.wakeup.is_set()


async def test_worker_honors_cancellation_after_media_preparation(tmp_path):
    item = MediaItem(url="https://example.com/image.png", filename="image.png", order=0)
    post = SourcePost(
        provider="direct", source_id="1", source_url=item.url, normalized_url=item.url,
        title="Title", author_name="Source", author_url="https://example.com",
        media_items=[item],
    )
    channel = SimpleNamespace(alias="main", publish_mode="auto", caption_template=None)
    job = SimpleNamespace(
        id=9, post_data=serialize_post(post), channel=channel,
        user_tags=[], source_tags=[], allow_duplicate=False,
    )
    jobs = SimpleNamespace(
        duplicate=AsyncMock(return_value=None),
        is_cancelled=AsyncMock(side_effect=[False, True]),
        transition=AsyncMock(),
    )
    pool = make_pool(jobs)
    downloaded = DownloadedMedia(item, tmp_path / "image.png", "image/png", 10)
    prepared = PreparedMedia(downloaded.path, as_document=False, order=0)
    pool.downloader.download.return_value = downloaded
    pool.media.prepare.return_value = prepared

    await pool._process(job)

    assert jobs.transition.await_args_list[-1].args == (job.id, JobStatus.CANCELLED)
    pool.publisher.publish.assert_not_awaited()


async def test_worker_snapshot_reports_only_current_job_as_busy():
    pool = make_pool(SimpleNamespace())
    waiting = asyncio.create_task(asyncio.Event().wait())
    pool.tasks = [waiting]
    pool._set_busy(0, 42, "artwork", "processing 1/2")

    snapshot = pool.snapshot()[0]

    assert snapshot.alive is True
    assert snapshot.job_id == 42
    assert snapshot.channel_alias == "artwork"
    assert snapshot.stage == "processing 1/2"
    assert snapshot.busy_seconds is not None

    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)
