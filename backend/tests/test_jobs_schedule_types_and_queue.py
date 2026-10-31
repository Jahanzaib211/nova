"""Two regressions the jobs refactor opened, pinned.

* ``jobs.enqueue`` became admin-only, but a non-admin could still run any job
  type by scheduling it every minute — schedules now share an allowlist.
* Agent tasks were hard-coded to the ``agents`` queue, which the default
  ``jobs.queues`` (``["default"]``) never claims, so they stayed queued forever.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deerflow.jobs.service import USER_SCHEDULABLE_JOB_TYPES, JobService


class _Repo:
    def __init__(self):
        self.upserts: list[dict] = []

    async def get_schedule_by_name(self, name):
        return None

    async def upsert_schedule(self, **kw):
        self.upserts.append(kw)
        return "s1"

    async def get_schedule(self, schedule_id):
        return {"id": schedule_id}


def _upsert(job_type: str, *, is_admin: bool):
    repo = _Repo()
    asyncio.run(JobService(repo).upsert_schedule(user_id="u1", name="n", job_type=job_type, cron="* * * * *", payload={}, is_admin=is_admin))
    return repo


def test_non_admin_can_schedule_their_own_kinds_of_work():
    for job_type in USER_SCHEDULABLE_JOB_TYPES:
        assert _upsert(job_type, is_admin=False).upserts, job_type


@pytest.mark.parametrize("job_type", ["em.events.prune", "em.bounce.poll", "em.campaign.batch", "made.up.type"])
def test_non_admin_cannot_schedule_maintenance_or_unknown_types(job_type):
    with pytest.raises(PermissionError):
        _upsert(job_type, is_admin=False)


def test_admin_can_schedule_anything():
    assert _upsert("em.events.prune", is_admin=True).upserts


@pytest.mark.parametrize(
    ("queues", "expected"),
    [(["default", "email", "agents"], "agents"), (["default"], "default"), (["email", "default"], "email")],
)
def test_agent_tasks_go_to_a_queue_a_worker_claims(monkeypatch, queues, expected):
    import deerflow.config as config_mod
    from deerflow.agents_registry.registry import agent_task_queue

    monkeypatch.setattr(config_mod, "get_app_config", lambda: SimpleNamespace(jobs=SimpleNamespace(queues=queues)))
    assert agent_task_queue() == expected
