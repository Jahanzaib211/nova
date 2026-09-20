"""Agents: the registry (lead, subagents, custom, ACP) and async delegation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Empty, Items, jobs_repo
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


class DelegateIn(BaseModel):
    agent: str = Field(description="Agent name from agents.registry")
    task: str = Field(description="What the agent should do; the result lands in outputs/agent-tasks/<job_id>.md")


class JobOut(BaseModel):
    job: dict[str, Any]


async def _registry(ctx: OpContext, inp: Empty) -> Items:
    from deerflow.agents_registry.registry import build_registry, live_counts
    from deerflow.config.app_config import get_app_config

    config = get_app_config()
    custom: list[Any] = []
    try:
        from deerflow.config.agents_config import list_custom_agents

        custom = list_custom_agents(user_id=ctx.user_id)
    except Exception:
        custom = []
    counts = await live_counts(jobs_repo(), ctx.user_id)
    agents = build_registry(config, custom_agents=custom, counts=counts)
    return Items(items=agents, total=len(agents))


async def _delegate(ctx: OpContext, inp: DelegateIn) -> JobOut:
    from deerflow.jobs.queue import JobQueue

    repo = jobs_repo()
    if repo is None:
        raise RuntimeError("agents: no database configured")
    job_id = await JobQueue(repo).enqueue("agents.task", {"agent": inp.agent, "task": inp.task}, owner_user_id=ctx.user_id, thread_id=ctx.thread_id)
    return JobOut(job=await repo.get(job_id) or {"id": job_id})


async def _status() -> ModuleStatus:
    from deerflow.config.app_config import get_app_config

    cfg = get_app_config()
    async_on = bool(getattr(getattr(cfg, "subagents", None), "async_enabled", False)) and bool(getattr(getattr(cfg, "jobs", None), "enabled", False))
    return ModuleStatus(configured=True, healthy=True, detail="async delegation on" if async_on else "async delegation off (subagents.async_enabled)")


MODULE = CapabilityModule(
    id="agents",
    title="Agents",
    config_key="subagents",
    description="Lead agent, built-in and custom subagents, ACP agents; async delegation as jobs.",
    status=_status,
    operations=[
        Operation(name="agents.registry", kind="read", input=Empty, output=Items, handler=_registry, description="All agents with their kind and the caller's live queued/running task counts."),
        Operation(name="agents.delegate", kind="execute", input=DelegateIn, output=JobOut, handler=_delegate, description="Run a task on an agent as a background job (survives reloads).", flag="jobs"),
    ],
)
