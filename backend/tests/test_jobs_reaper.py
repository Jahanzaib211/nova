"""The reaper returns leases nobody heartbeats: retry, then dead-letter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from deerflow.jobs.queue import JobQueue
from deerflow.jobs.status import JobStatus
from deerflow.jobs.worker import reap_expired_leases
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


@pytest.mark.anyio
async def test_expired_lease_is_retried_then_dead_lettered(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    job_id = await queue.enqueue("a", {}, max_attempts=2)
    await repo.claim(queues=["default"], worker_id="w1", lease_ttl=timedelta(seconds=1))
    future = datetime.now(UTC) + timedelta(minutes=5)
    reaped = await reap_expired_leases(repo, now=future, backoff_seconds=1)
    assert reaped == [job_id]
    assert (await repo.get(job_id))["status"] == JobStatus.RETRYING.value
    await repo.claim(queues=["default"], worker_id="w1", lease_ttl=timedelta(seconds=1), now=future + timedelta(minutes=1))
    reaped = await reap_expired_leases(repo, now=future + timedelta(minutes=10), backoff_seconds=1)
    assert reaped == [job_id]
    assert (await repo.get(job_id))["status"] == JobStatus.DEAD_LETTER.value


@pytest.mark.anyio
async def test_live_lease_is_left_alone(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    job_id = await queue.enqueue("a", {})
    await repo.claim(queues=["default"], worker_id="w1", lease_ttl=timedelta(minutes=5))
    assert await reap_expired_leases(repo, now=datetime.now(UTC), backoff_seconds=1) == []
    assert (await repo.get(job_id))["status"] == JobStatus.LEASED.value
