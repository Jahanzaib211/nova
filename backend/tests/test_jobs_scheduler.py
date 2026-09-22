"""Cron schedules: next-run computation, due detection, dedupe, catch-up cap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from deerflow.jobs.scheduler import next_run_at, tick_schedules
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


def test_next_run_at_follows_cron_in_the_given_timezone():
    base = datetime(2026, 9, 19, 10, 30, tzinfo=UTC)
    assert next_run_at("*/5 * * * *", base) == datetime(2026, 9, 19, 10, 35, tzinfo=UTC)
    assert next_run_at("0 3 * * *", base, timezone="Asia/Karachi").hour == 22  # 03:00 PKT == 22:00 UTC previous day


def test_next_run_at_rejects_bad_expressions():
    with pytest.raises(ValueError):
        next_run_at("not a cron", datetime.now(UTC))


@pytest.mark.anyio
async def test_tick_enqueues_due_schedule_once_and_advances(tmp_path):
    repo = await _repo(tmp_path)
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    sched_id = await repo.upsert_schedule(name="nightly", job_type="demo.sleep", cron="0 * * * *", payload={"n": 1}, queue="default", enabled=True, next_run_at=now - timedelta(minutes=1))
    enqueued = await tick_schedules(repo, now=now)
    assert len(enqueued) == 1
    again = await tick_schedules(repo, now=now)
    assert again == [], "advanced next_run_at, nothing due"
    sched = await repo.get_schedule(sched_id)
    assert sched["next_run_at"] == datetime(2026, 9, 19, 13, 0, tzinfo=UTC)
    assert sched["last_enqueued_for"] == now - timedelta(minutes=1)
    jobs = await repo.list_jobs()
    assert jobs[0]["schedule_id"] == sched_id and jobs[0]["dedupe_key"].startswith("sched:")


@pytest.mark.anyio
async def test_tick_skips_disabled_and_caps_catch_up(tmp_path):
    repo = await _repo(tmp_path)
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    await repo.upsert_schedule(name="off", job_type="a", cron="* * * * *", payload={}, queue="default", enabled=False, next_run_at=now - timedelta(days=1))
    await repo.upsert_schedule(name="stale", job_type="a", cron="* * * * *", payload={}, queue="default", enabled=True, next_run_at=now - timedelta(days=1))
    enqueued = await tick_schedules(repo, now=now)
    assert len(enqueued) == 1, "one catch-up run for a schedule that missed many windows, none for the disabled one"
    sched = await repo.get_schedule_by_name("stale")
    assert sched["next_run_at"] > now
