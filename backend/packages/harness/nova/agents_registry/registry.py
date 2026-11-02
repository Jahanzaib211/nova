from __future__ import annotations

from typing import Any

from deerflow.persistence.job.sql import JobRepository

AGENT_TASK_JOB = "agents.task"
#: The worker queue agent tasks run on (``jobs.queues`` must list it).
AGENT_TASK_QUEUE = "agents"


def async_delegation_enabled(config: Any) -> bool:
    """Whether agent tasks can run as background jobs: ``subagents.async_enabled`` and ``jobs.enabled``."""
    return bool(getattr(getattr(config, "subagents", None), "async_enabled", False)) and bool(getattr(getattr(config, "jobs", None), "enabled", False))


def agent_task_queue() -> str:
    """The queue agent tasks go to: ``agents`` when a worker claims it, else ``default``.

    ``jobs.queues`` defaults to ``["default"]``, so hard-coding ``agents`` left
    every delegated task ``queued`` forever on a deployment that never listed
    that queue — the worker claims only what config names.
    """
    try:
        from deerflow.config import get_app_config

        queues = list(getattr(get_app_config().jobs, "queues", None) or ["default"])
    except Exception:  # noqa: BLE001 - no config means the defaults
        queues = ["default"]
    return AGENT_TASK_QUEUE if AGENT_TASK_QUEUE in queues else (queues[0] if queues else "default")


async def enqueue_agent_task(repo: JobRepository, *, agent: str, kind: str, task: str, owner_user_id: str | None, thread_id: str | None, model: str | None = None) -> str:
    """Enqueue one ``agents.task`` job; the only enqueue path for agent work.

    One attempt, on the ``agents`` queue (or the first configured one, see :func:`agent_task_queue`). An agent run has side effects
    (files written, messages sent, commands run), so the runner's default
    retry budget would replay them; ``agents.delegate`` used to enqueue on
    ``default`` with that budget (5 attempts) while ``delegate_async`` used
    one — the same task was idempotent or not depending on who asked.
    """
    from deerflow.jobs.queue import JobQueue

    payload: dict[str, Any] = {"agent": agent, "kind": kind, "task": task}
    if model:
        payload["model"] = model
    return await JobQueue(repo).enqueue(AGENT_TASK_JOB, payload, queue=agent_task_queue(), owner_user_id=owner_user_id, thread_id=thread_id, max_attempts=1)


def _counts_by_agent(jobs: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for job in jobs:
        if job.get("type") != AGENT_TASK_JOB:
            continue
        agent = str((job.get("payload") or {}).get("agent") or "")
        bucket = out.setdefault(agent, {"queued": 0, "running": 0})
        status = job.get("status")
        if status in ("queued", "retrying"):
            bucket["queued"] += 1
        elif status in ("leased", "running"):
            bucket["running"] += 1
    return out


async def live_counts(repo: JobRepository | None, owner_user_id: str | None) -> dict[str, dict[str, int]]:
    if repo is None:
        return {}
    jobs: list[dict[str, Any]] = []
    for status in ("queued", "retrying", "leased", "running"):
        jobs.extend(await repo.list_jobs(owner_user_id=owner_user_id, status=status, job_type=AGENT_TASK_JOB, limit=500))
    return _counts_by_agent(jobs)


def build_registry(config: Any, *, custom_agents: list[Any] | None = None, counts: dict[str, dict[str, int]] | None = None) -> list[dict[str, Any]]:
    """Every agent Nova can run, in the shape the UI renders."""
    counts = counts or {}
    entries: list[dict[str, Any]] = []
    lead_model = config.models[0].name if getattr(config, "models", None) else None
    entries.append({"id": "lead", "kind": "lead", "name": "Nova", "description": "The lead agent every chat talks to.", "model": lead_model, "runner": "gateway", "async_capable": False, **counts.get("lead", {"queued": 0, "running": 0})})

    from deerflow.subagents.registry import BUILTIN_SUBAGENTS

    subagents = getattr(config, "subagents", None)
    overrides = getattr(subagents, "agents", {}) or {}
    for name, cfg in BUILTIN_SUBAGENTS.items():
        override = overrides.get(name)
        entries.append(
            {
                "id": f"subagent:{name}",
                "kind": "subagent",
                "name": name,
                "description": getattr(cfg, "description", ""),
                "model": (getattr(override, "model", None) if override else None) or getattr(cfg, "model", "inherit"),
                "runner": "jobs" if getattr(subagents, "async_enabled", False) else "gateway",
                "async_capable": True,
                **counts.get(name, {"queued": 0, "running": 0}),
            }
        )
    for name, cfg in (getattr(subagents, "custom_agents", {}) or {}).items():
        entries.append(
            {
                "id": f"subagent:{name}",
                "kind": "subagent",
                "name": name,
                "description": cfg.description,
                "model": cfg.model,
                "runner": "jobs" if getattr(subagents, "async_enabled", False) else "gateway",
                "async_capable": True,
                **counts.get(name, {"queued": 0, "running": 0}),
            }
        )
    for agent in custom_agents or []:
        entries.append(
            {"id": f"custom:{agent.name}", "kind": "custom", "name": agent.name, "description": agent.description, "model": agent.model, "runner": "gateway", "async_capable": False, **counts.get(agent.name, {"queued": 0, "running": 0})}
        )
    for name, cfg in (getattr(config, "acp_agents", {}) or {}).items():
        entries.append(
            {
                "id": f"acp:{name}",
                "kind": "acp",
                "name": name,
                "description": cfg.description,
                "model": cfg.model,
                "runner": "jobs" if getattr(subagents, "async_enabled", False) else "gateway",
                "async_capable": True,
                **counts.get(name, {"queued": 0, "running": 0}),
            }
        )
    return entries
