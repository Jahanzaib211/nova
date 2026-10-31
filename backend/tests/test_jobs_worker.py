"""One worker pass: claim → run handler → outcome, with heartbeats and cancellation."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from deerflow.jobs.context import JobContext
from deerflow.jobs.errors import JobCancelled, RetryableError
from deerflow.jobs.queue import JobQueue
from deerflow.jobs.registry import JobRegistry
from deerflow.jobs.status import JobEventType, JobStatus
from deerflow.jobs.worker import Worker, WorkerSettings
from deerflow.persistence.job.sql import JobRepository

pytestmark = pytest.mark.no_auto_user


async def _repo(tmp_path) -> JobRepository:
    from deerflow.persistence.engine import get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}", sqlite_dir=str(tmp_path))
    return JobRepository(get_session_factory())


@pytest.fixture(autouse=True)
def _close_engine_after_test():
    yield
    import asyncio

    from deerflow.persistence.engine import close_engine

    asyncio.run(close_engine())


def _worker(repo, registry, **overrides):
    settings = WorkerSettings(worker_id="w-test", queues=["default"], concurrency=2, lease_ttl=timedelta(seconds=30), poll_interval=0.01, **overrides)
    return Worker(repo, registry, settings)


@pytest.mark.anyio
async def test_handler_success_records_result_and_progress(tmp_path):
    repo = await _repo(tmp_path)
    registry = JobRegistry()

    async def handler(ctx: JobContext):
        await ctx.progress(50, "half")
        await ctx.log("hello")
        return {"answer": ctx.payload["x"] * 2}

    registry.register("double", handler)
    queue = JobQueue(repo)
    job_id = await queue.enqueue("double", {"x": 21})
    worker = _worker(repo, registry)
    assert await worker.run_once() == 1
    job = await repo.get(job_id)
    assert job["status"] == JobStatus.SUCCEEDED.value
    assert job["result"] == {"answer": 42}
    assert job["progress_pct"] == 100
    types = [e["type"] for e in await repo.list_events(job_id)]
    assert types == [JobEventType.ENQUEUED.value, JobEventType.LEASED.value, JobEventType.PROGRESS.value, JobEventType.LOG.value, JobEventType.SUCCEEDED.value]


@pytest.mark.anyio
async def test_retryable_error_schedules_retry_and_other_errors_fail(tmp_path):
    repo = await _repo(tmp_path)
    registry = JobRegistry()

    async def flaky(ctx):
        raise RetryableError("upstream 503", delay=5)

    async def broken(ctx):
        raise RuntimeError("bug")

    registry.register("flaky", flaky)
    registry.register("broken", broken)
    queue = JobQueue(repo)
    a = await queue.enqueue("flaky", {})
    b = await queue.enqueue("broken", {})
    worker = _worker(repo, registry)
    await worker.run_once()
    assert (await repo.get(a))["status"] == JobStatus.RETRYING.value
    assert (await repo.get(b))["status"] == JobStatus.FAILED.value
    assert "bug" in (await repo.get(b))["error"]


@pytest.mark.anyio
async def test_unknown_type_dead_letters_immediately(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    job_id = await queue.enqueue("nobody.handles.this", {})
    worker = _worker(repo, JobRegistry())
    await worker.run_once()
    job = await repo.get(job_id)
    assert job["status"] == JobStatus.DEAD_LETTER.value
    assert "no handler" in job["error"]


@pytest.mark.anyio
async def test_cancel_request_stops_a_cooperative_handler(tmp_path):
    repo = await _repo(tmp_path)
    registry = JobRegistry()
    started = asyncio.Event()

    async def slow(ctx: JobContext):
        started.set()
        for _ in range(200):
            await ctx.heartbeat()  # raises JobCancelled once requested
            await asyncio.sleep(0.01)
        return "finished"

    registry.register("slow", slow)
    queue = JobQueue(repo)
    job_id = await queue.enqueue("slow", {})
    worker = _worker(repo, registry)
    task = asyncio.create_task(worker.run_once())
    await started.wait()
    await repo.request_cancel(job_id)
    await task
    job = await repo.get(job_id)
    assert job["status"] == JobStatus.CANCELLED.value


@pytest.mark.anyio
async def test_context_heartbeat_raises_when_cancelled(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    job_id = await queue.enqueue("x", {})
    [job] = await repo.claim(queues=["default"], worker_id="w", lease_ttl=timedelta(seconds=5))
    ctx = JobContext(repo, job, worker_id="w", lease_ttl=timedelta(seconds=5))
    await ctx.heartbeat()
    await repo.request_cancel(job_id)
    with pytest.raises(JobCancelled):
        await ctx.heartbeat()


@pytest.mark.anyio
async def test_worker_registers_and_heartbeats_itself(tmp_path):
    repo = await _repo(tmp_path)
    worker = _worker(repo, JobRegistry())
    await worker.announce()
    [row] = await repo.list_workers()
    assert row["worker_id"] == "w-test" and row["queues"] == ["default"]
