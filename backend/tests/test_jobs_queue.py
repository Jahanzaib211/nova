"""Job runner core: enqueue, dedupe, claim/lease, heartbeat, cancel, outcomes.

Everything here runs against SQLite (the test backend); the claim query is
written so the same statement takes ``FOR UPDATE SKIP LOCKED`` on Postgres
and degrades to SQLite's single-writer semantics. Statuses and event types
are the contract in ``contracts/job_status_contract.json``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from deerflow.jobs.queue import JobQueue
from deerflow.jobs.status import JobEventType, JobStatus
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
async def test_enqueue_records_row_and_event(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    job_id = await queue.enqueue("demo.sleep", {"seconds": 1}, owner_user_id="u1", thread_id="t1")
    job = await repo.get(job_id)
    assert job["status"] == JobStatus.QUEUED.value
    assert job["type"] == "demo.sleep"
    assert job["payload"] == {"seconds": 1}
    assert job["owner_user_id"] == "u1"
    assert job["attempts"] == 0
    events = await repo.list_events(job_id)
    assert [e["type"] for e in events] == [JobEventType.ENQUEUED.value]


@pytest.mark.anyio
async def test_dedupe_key_returns_the_live_job(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    a = await queue.enqueue("demo.sleep", {}, dedupe_key="k1")
    b = await queue.enqueue("demo.sleep", {}, dedupe_key="k1")
    assert a == b
    await repo.mark_succeeded(a, result={"ok": True})
    c = await queue.enqueue("demo.sleep", {}, dedupe_key="k1")
    assert c != a, "a terminal job no longer blocks the key"


@pytest.mark.anyio
async def test_claim_respects_queue_priority_and_run_after(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    later = datetime.now(UTC) + timedelta(hours=1)
    await queue.enqueue("a", {}, queue="default", priority=0)
    hi = await queue.enqueue("b", {}, queue="default", priority=10)
    await queue.enqueue("c", {}, queue="default", run_after=later)
    await queue.enqueue("d", {}, queue="email")
    claimed = await repo.claim(queues=["default"], worker_id="w1", lease_ttl=timedelta(seconds=30), limit=5)
    assert [j["id"] for j in claimed][0] == hi, "highest priority first"
    assert len(claimed) == 2, "the future job and the other queue are not claimed"
    for j in claimed:
        assert j["status"] == JobStatus.LEASED.value
        assert j["lease_owner"] == "w1"
        assert j["lease_expires_at"] > datetime.now(UTC)
    again = await repo.claim(queues=["default"], worker_id="w2", lease_ttl=timedelta(seconds=30), limit=5)
    assert again == [], "a leased job is not claimed twice"


@pytest.mark.anyio
async def test_start_heartbeat_and_cancel(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    job_id = await queue.enqueue("a", {})
    [job] = await repo.claim(queues=["default"], worker_id="w1", lease_ttl=timedelta(seconds=30))
    await repo.mark_running(job_id, worker_id="w1")
    assert (await repo.get(job_id))["status"] == JobStatus.RUNNING.value
    before = (await repo.get(job_id))["lease_expires_at"]
    extended = await repo.heartbeat(job_id, worker_id="w1", lease_ttl=timedelta(seconds=120))
    assert extended is True
    assert (await repo.get(job_id))["lease_expires_at"] > before
    assert await repo.heartbeat(job_id, worker_id="other", lease_ttl=timedelta(seconds=120)) is False, "another worker cannot extend a lease it does not own"
    await repo.request_cancel(job_id)
    assert (await repo.get(job_id))["cancel_requested"] is True
    assert await repo.is_cancel_requested(job_id) is True
    await repo.mark_cancelled(job_id)
    types = [e["type"] for e in await repo.list_events(job_id)]
    assert types[-2:] == [JobEventType.CANCEL_REQUESTED.value, JobEventType.CANCELLED.value]


@pytest.mark.anyio
async def test_progress_and_outcomes(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    job_id = await queue.enqueue("a", {})
    await repo.claim(queues=["default"], worker_id="w1", lease_ttl=timedelta(seconds=30))
    await repo.mark_running(job_id, worker_id="w1")
    await repo.progress(job_id, pct=40, message="halfway", data={"n": 2})
    job = await repo.get(job_id)
    assert (job["progress_pct"], job["progress_message"]) == (40, "halfway")
    await repo.mark_failed(job_id, error="boom")
    job = await repo.get(job_id)
    assert job["status"] == JobStatus.FAILED.value and job["error"] == "boom"
    assert job["finished_at"] is not None
    events = await repo.list_events(job_id, since_seq=0)
    assert [e["type"] for e in events][-2:] == [JobEventType.PROGRESS.value, JobEventType.FAILED.value]
    assert events[-2]["payload"] == {"pct": 40, "message": "halfway", "data": {"n": 2}}


@pytest.mark.anyio
async def test_retry_scheduling_uses_backoff_and_dead_letters_at_max(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    job_id = await queue.enqueue("a", {}, max_attempts=2)
    await repo.claim(queues=["default"], worker_id="w1", lease_ttl=timedelta(seconds=30))
    outcome = await repo.schedule_retry(job_id, error="try again", backoff_seconds=30)
    job = await repo.get(job_id)
    assert outcome == JobStatus.RETRYING.value
    assert job["attempts"] == 1 and job["status"] == JobStatus.RETRYING.value
    assert job["run_after"] > datetime.now(UTC) + timedelta(seconds=20)
    # Second failure exhausts max_attempts=2 → dead letter.
    await repo.claim(queues=["default"], worker_id="w1", lease_ttl=timedelta(seconds=30), now=job["run_after"] + timedelta(seconds=1))
    outcome = await repo.schedule_retry(job_id, error="again", backoff_seconds=30)
    job = await repo.get(job_id)
    assert outcome == JobStatus.DEAD_LETTER.value
    assert job["status"] == JobStatus.DEAD_LETTER.value
    types = [e["type"] for e in await repo.list_events(job_id)]
    assert JobEventType.RETRY_SCHEDULED.value in types and types[-1] == JobEventType.DEAD_LETTERED.value


@pytest.mark.anyio
async def test_list_for_owner_and_counts(tmp_path):
    repo = await _repo(tmp_path)
    queue = JobQueue(repo)
    await queue.enqueue("a", {}, owner_user_id="u1")
    await queue.enqueue("b", {}, owner_user_id="u2")
    mine = await repo.list_jobs(owner_user_id="u1")
    assert [j["type"] for j in mine] == ["a"]
    summary = await repo.summary()
    assert summary["queued"] == 2 and summary["running"] == 0
