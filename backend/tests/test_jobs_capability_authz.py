"""``jobs.*`` capability ops apply the same owner and state rules as
``/api/jobs`` (both go through ``deerflow.jobs.service``).

Before the shared service the capability let any caller take over another
owner's schedule by name, cancel/retry system jobs and delete system
schedules (owner ``None`` passed its owner check), left queued jobs waiting
on a worker after a cancel, and exposed ``jobs.workers`` / ``jobs.enqueue``
to every user.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deerflow.capabilities import CapabilityRegistry, OpContext, OperationConflict, OperationDenied
from deerflow.capabilities.modules import jobs as jobs_module
from deerflow.jobs.queue import JobQueue
from deerflow.jobs.service import JobConflict, JobNotFound, JobService
from deerflow.persistence.base import Base
from deerflow.persistence.job.sql import JobRepository


@pytest.fixture()
def repo(monkeypatch):
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    r = JobRepository(sf)
    monkeypatch.setattr(jobs_module, "jobs_repo", lambda: r)
    yield r
    asyncio.run(engine.dispose())


def _registry() -> CapabilityRegistry:
    reg = CapabilityRegistry(flags=lambda: {"jobs": True})
    reg.register(jobs_module.MODULE)
    return reg


def _ctx(user: str, *, admin: bool = False) -> OpContext:
    return OpContext(user_id=user, is_admin=admin)


def _invoke(name: str, ctx: OpContext, payload: dict):
    return asyncio.run(_registry().invoke(name, ctx, payload))


def _enqueue(repo, owner: str | None) -> str:
    return asyncio.run(JobQueue(repo).enqueue("sample", {}, owner_user_id=owner))


def _schedule(name: str = "nightly") -> dict:
    return {"name": name, "type": "jobs.smoke.sleep", "cron": "0 3 * * *"}


def test_schedule_upsert_refuses_a_name_owned_by_someone_else(repo):
    _invoke("jobs.schedule_upsert", _ctx("alice"), _schedule())
    with pytest.raises(OperationConflict):
        _invoke("jobs.schedule_upsert", _ctx("mallory"), _schedule())
    sched = asyncio.run(repo.get_schedule_by_name("nightly"))
    assert sched["owner_user_id"] == "alice", "the schedule must not change hands"


def test_schedule_upsert_refuses_a_system_schedule_name(repo):
    asyncio.run(repo.upsert_schedule(name="system-prune", job_type="sample", cron="0 0 * * *", payload={}, owner_user_id=None))
    with pytest.raises(OperationConflict):
        _invoke("jobs.schedule_upsert", _ctx("mallory"), _schedule("system-prune"))
    assert asyncio.run(repo.get_schedule_by_name("system-prune"))["owner_user_id"] is None


def test_schedule_upsert_still_updates_the_callers_own_schedule(repo):
    _invoke("jobs.schedule_upsert", _ctx("alice"), _schedule())
    out = _invoke("jobs.schedule_upsert", _ctx("alice"), {**_schedule(), "cron": "0 4 * * *"})
    assert out["job"]["cron"] == "0 4 * * *"


def test_system_jobs_and_schedules_are_admin_only(repo):
    system_job = _enqueue(repo, None)
    sid = asyncio.run(repo.upsert_schedule(name="sys", job_type="sample", cron="0 0 * * *", payload={}, owner_user_id=None))
    for name, payload in (("jobs.get", {"job_id": system_job}), ("jobs.cancel", {"job_id": system_job}), ("jobs.retry", {"job_id": system_job}), ("jobs.schedule_delete", {"schedule_id": sid})):
        with pytest.raises(LookupError):
            _invoke(name, _ctx("mallory"), payload)
    assert asyncio.run(repo.get(system_job))["status"] == "queued"
    assert asyncio.run(repo.get_schedule(sid)) is not None
    assert _invoke("jobs.schedule_delete", _ctx("root", admin=True), {"schedule_id": sid}) == {"ok": True, "detail": None}


def test_cancel_settles_queued_jobs_and_conflicts_on_terminal_ones(repo):
    job = _enqueue(repo, "alice")
    assert _invoke("jobs.cancel", _ctx("alice"), {"job_id": job}) == {"ok": True, "detail": "cancelled"}
    assert asyncio.run(repo.get(job))["status"] == "cancelled"
    with pytest.raises(OperationConflict):
        _invoke("jobs.cancel", _ctx("alice"), {"job_id": job})


def test_cancel_of_a_running_job_is_cooperative(repo):
    job = _enqueue(repo, "alice")
    asyncio.run(repo.claim(queues=["default"], worker_id="w", lease_ttl=timedelta(seconds=30)))
    asyncio.run(repo.mark_running(job, worker_id="w"))
    assert _invoke("jobs.cancel", _ctx("alice"), {"job_id": job}) == {"ok": True, "detail": "running"}
    assert asyncio.run(repo.get(job))["cancel_requested"] is True


def test_retry_conflicts_unless_failed(repo):
    job = _enqueue(repo, "alice")
    with pytest.raises(OperationConflict):
        _invoke("jobs.retry", _ctx("alice"), {"job_id": job})
    asyncio.run(repo.mark_failed(job, error="boom"))
    assert _invoke("jobs.retry", _ctx("alice"), {"job_id": job})["ok"] is True


def test_non_admin_enqueues_only_user_schedulable_types(repo):
    # The harness and MCP surfaces never run as admin, so an admin-only
    # enqueue took the tool away from every agent. The schedule allowlist
    # bounds it instead: same rule, so enqueue and schedule cannot drift.
    job = _invoke("jobs.enqueue", _ctx("alice"), {"type": "jobs.smoke.sleep"})["job"]
    assert job["owner_user_id"] == "alice"
    for job_type in ("em.events.prune", "em.campaign.batch", "made.up.type"):
        with pytest.raises(PermissionError):
            _invoke("jobs.enqueue", _ctx("alice"), {"type": job_type})


def test_admin_enqueues_any_type(repo):
    assert _invoke("jobs.enqueue", _ctx("root", admin=True), {"type": "sample"})["job"]["owner_user_id"] == "root"


def test_workers_is_readable_by_non_admins(repo):
    assert _invoke("jobs.workers", _ctx("alice"), {})["total"] == 0


def test_summary_stays_admin_only(repo):
    with pytest.raises(OperationDenied):
        _invoke("jobs.summary", _ctx("alice"), {})


def test_agents_hold_enqueue_and_workers_tools():
    from deerflow.tools.capability_tools import build_capability_tools

    names = {t.name for t in build_capability_tools(_registry(), enabled_flags={"jobs"}, groups={"nova:jobs"})}
    assert {"jobs__enqueue", "jobs__workers"} <= names
    assert "jobs__summary" not in names


def test_service_owner_check_hides_other_owners(repo):
    job = _enqueue(repo, "bob")
    svc = JobService(repo)
    with pytest.raises(JobNotFound):
        asyncio.run(svc.owned_job(job, user_id="alice"))
    assert asyncio.run(svc.owned_job(job, user_id="alice", is_admin=True))["id"] == job
    with pytest.raises(ValueError):
        asyncio.run(svc.upsert_schedule(user_id="alice", name="bad", job_type="sample", cron="not a cron", payload={}))
    with pytest.raises(ValueError):
        asyncio.run(svc.upsert_schedule(user_id="alice", name="bad", job_type="sample", cron="0 0 * * *", payload={}, timezone="Mars/Olympus"))
    assert issubclass(JobConflict, Exception)
