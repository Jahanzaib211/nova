"""Agents: the registry (lead, subagents, custom, ACP) and async delegation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Empty, Items, jobs_repo
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


class DelegateIn(BaseModel):
    agent: str = Field(description="Agent from agents.registry: its id (`subagent:bash`, `acp:claude_code`) or bare name (`bash`, `claude_code`).")
    task: str = Field(description="What the agent should do; the result lands in outputs/agent-tasks/<job_id>.md")
    model: str | None = Field(default=None, description="Model for a subagent (name from models.list). Default: the deployment's default model.")


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


def resolve_delegate_target(agent: str, config: Any) -> tuple[str, str]:
    """``(name, kind)`` for what the caller named, or ``ValueError``.

    Accepts the registry id (``acp:claude_code``) as well as the bare name,
    because the registry is what a caller reads first. Until 2026-09-22 the
    job was enqueued without a ``kind`` at all, so the worker treated every
    target as a subagent and answered ``unknown subagent 'claude_code'``.
    """
    from deerflow.tools.builtins.delegate_async_tool import _known_agents

    known = _known_agents(config)
    name = agent.strip()
    for prefix in ("acp:", "subagent:"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    kind = known.get(name)
    if kind is None:
        raise ValueError(f"unknown agent {agent!r}. Available: {', '.join(f'{k} ({v})' for k, v in known.items()) or 'none'}")
    return name, kind


def delegate_model_for_runtime(config: Any) -> str | None:
    """The model a subagent runs on when an ACP runtime delegates to it.

    A chat on Claude Code that hands work to a Nova subagent otherwise gets
    the deployment default — on this box MiniMax, a different provider with
    its own quota, which is exactly how "Claude" ended up waiting on a
    MiniMax 429 (live 2026-09-22). ``runtimes.delegate_model`` pins it.
    """
    runtimes = getattr(config, "runtimes", None)
    model = getattr(runtimes, "delegate_model", None) if runtimes is not None else None
    return str(model) if model else None


async def _delegate(ctx: OpContext, inp: DelegateIn) -> JobOut:
    from deerflow.config.app_config import get_app_config
    from deerflow.jobs.queue import JobQueue

    repo = jobs_repo()
    if repo is None:
        raise RuntimeError("agents: no database configured")
    config = get_app_config()
    name, kind = resolve_delegate_target(inp.agent, config)
    payload: dict[str, Any] = {"agent": name, "kind": kind, "task": inp.task}
    model = inp.model or (delegate_model_for_runtime(config) if ctx.surface == "mcp" else None)
    if model:
        payload["model"] = model
    job_id = await JobQueue(repo).enqueue("agents.task", payload, owner_user_id=ctx.user_id, thread_id=ctx.thread_id)
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
