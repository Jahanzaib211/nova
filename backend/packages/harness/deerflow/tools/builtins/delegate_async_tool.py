"""``delegate_async`` / ``check_delegation``: hand a task to a subagent or
ACP agent as an ``agents.task`` job and come back for the result.

Unlike ``task`` (which blocks the run and dies with a gateway reload), the
work happens in the jobs container and survives restarts; the lead agent
polls with ``check_delegation`` and the user sees it on the Jobs page.
Gated by ``subagents.async_enabled``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, InjectedToolArg, StructuredTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

AGENT_TASK_JOB = "agents.task"
QUEUE = "agents"


class _DelegateInput(BaseModel):
    agent: str = Field(description="Subagent name (e.g. general-purpose) or ACP agent name (e.g. claude_code)")
    task: str = Field(description="Self-contained task description; the agent has no access to this conversation")


class _CheckInput(BaseModel):
    job_id: str = Field(description="The id returned by delegate_async")


def _known_agents(config: Any) -> dict[str, str]:
    from deerflow.subagents.registry import get_subagent_names

    out = {name: "subagent" for name in get_subagent_names(app_config=config)}
    for name in getattr(config, "acp_agents", {}) or {}:
        out[name] = "acp"
    return out


def build_delegate_tools(config: Any, repo_factory: Any) -> list[BaseTool]:
    """``repo_factory()`` returns a JobRepository (the gateway's session factory)."""
    from deerflow.jobs.queue import JobQueue
    from deerflow.runtime.user_context import get_effective_user_id

    agents = _known_agents(config)
    listing = "\n".join(f"- {name} ({kind})" for name, kind in agents.items()) or "- (none configured)"

    async def _delegate(agent: str, task: str, config_: Annotated[RunnableConfig, InjectedToolArg] = None) -> str:
        if agent not in agents:
            return f"Error: unknown agent '{agent}'. Available: {', '.join(agents) or 'none'}"
        repo = repo_factory()
        if repo is None:
            return "Error: the job runner is not available (jobs.enabled is false or the database is down)."
        thread_id = ((config_ or {}).get("configurable") or {}).get("thread_id")
        try:
            owner = get_effective_user_id()
        except Exception:
            owner = None
        job_id = await JobQueue(repo).enqueue(AGENT_TASK_JOB, {"agent": agent, "kind": agents[agent], "task": task}, queue=QUEUE, owner_user_id=owner, thread_id=thread_id, max_attempts=1)
        return f"Delegated to {agent} as job {job_id}. Call check_delegation with this id to get the result; the user can follow it on the Jobs page."

    async def _check(job_id: str) -> str:
        repo = repo_factory()
        if repo is None:
            return "Error: the job runner is not available."
        job = await repo.get(job_id)
        if job is None or job.get("type") != AGENT_TASK_JOB:
            return f"Error: no delegation with id {job_id}"
        status = job["status"]
        if status == "succeeded":
            result = (job.get("result_json") or {}).get("result") or "(no output)"
            return f"Status: succeeded\n\n{result}"
        if status in ("failed", "dead_letter", "cancelled"):
            return f"Status: {status}. {job.get('error') or ''}".strip()
        return f"Status: {status} ({job.get('progress_pct', 0)}%): {job.get('progress_message') or 'working'}"

    delegate = StructuredTool.from_function(
        name="delegate_async",
        description=("Delegate a self-contained task to another agent as a background job that survives restarts. Returns a job id immediately; poll with check_delegation.\n\nAvailable agents:\n" + listing),
        coroutine=_delegate,
        args_schema=_DelegateInput,
    )
    check = StructuredTool.from_function(
        name="check_delegation",
        description="Check a delegate_async job: status, progress, and the result once it has succeeded.",
        coroutine=_check,
        args_schema=_CheckInput,
    )
    return [delegate, check]
