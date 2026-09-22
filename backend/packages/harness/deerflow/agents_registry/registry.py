from __future__ import annotations

from typing import Any

from deerflow.persistence.job.sql import JobRepository

AGENT_TASK_JOB = "agents.task"


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
