"""``agents.task``: run a subagent or ACP agent as a job.

The handler mirrors what the ``task`` tool does in-process, but in the jobs
container: the result survives a gateway reload, the user follows progress
on the Jobs page, and the lead agent collects it with ``check_delegation``.
The result is also written to ``outputs/agent-tasks/<job_id>.md`` in the
thread's workspace when the job carries a thread id.

Limits the worker imposes: there is no sandbox provider here (no docker
socket), so a subagent whose tools need the sandbox (bash, file tools)
fails at the first such call with a clear error. ACP agents run in their own
workspace and are unaffected.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from deerflow.config.app_config import get_app_config
from deerflow.jobs.context import JobContext
from deerflow.jobs.registry import JobRegistry

logger = logging.getLogger(__name__)

AGENT_TASK_JOB = "agents.task"


def _write_artifact(ctx: JobContext, agent: str, task: str, result: str) -> str | None:
    if not ctx.thread_id:
        return None
    try:
        from deerflow.config.paths import get_paths

        out_dir = get_paths().thread_dir(ctx.thread_id, user_id=ctx.owner_user_id) / "user-data" / "outputs" / "agent-tasks"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{ctx.job_id}.md"
        path.write_text(f"# {agent}\n\n## Task\n\n{task}\n\n## Result\n\n{result}\n", encoding="utf-8")
        return f"/mnt/user-data/outputs/agent-tasks/{ctx.job_id}.md"
    except Exception as exc:
        logger.warning("agents.task: could not write artifact: %s", exc)
        return None


async def _run_subagent(ctx: JobContext, agent: str, task: str) -> str:
    from deerflow.subagents import SubagentExecutor, get_subagent_config
    from deerflow.subagents.executor import SubagentStatus, get_background_task_result
    from deerflow.tools import get_available_tools

    config = get_app_config()
    sub_cfg = get_subagent_config(agent, app_config=config)
    if sub_cfg is None:
        raise RuntimeError(f"unknown subagent {agent!r}")
    tools = get_available_tools(subagent_enabled=False, app_config=config)
    executor = SubagentExecutor(config=sub_cfg, tools=tools, app_config=config, thread_id=ctx.thread_id, user_id=ctx.owner_user_id)
    task_id = executor.execute_async(task, task_id=ctx.job_id)
    seen = 0
    while True:
        await ctx.heartbeat()
        result = get_background_task_result(task_id)
        if result is None:
            await asyncio.sleep(2)
            continue
        messages = getattr(result, "ai_messages", None) or []
        if len(messages) > seen:
            seen = len(messages)
            last = messages[-1]
            text = last.get("content") if isinstance(last, dict) else str(last)
            await ctx.progress(min(90, 10 + seen * 5), (str(text) if text else f"turn {seen}")[:200])
        if result.status in (SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.TIMED_OUT, SubagentStatus.CANCELLED):
            break
        await asyncio.sleep(2)
    if result.status != SubagentStatus.COMPLETED:
        raise RuntimeError(result.error or f"subagent {result.status.value}")
    return result.result or ""


async def _run_acp(ctx: JobContext, agent: str, task: str) -> str:
    from deerflow.config.acp_config import get_acp_agents
    from deerflow.tools.builtins.invoke_acp_agent_tool import build_invoke_acp_agent_tool

    agents = get_acp_agents()
    if agent not in agents:
        raise RuntimeError(f"unknown ACP agent {agent!r}")
    tool = build_invoke_acp_agent_tool(agents)
    await ctx.progress(10, f"{agent} started")
    result = await tool.coroutine(agent=agent, prompt=task, config={"configurable": {"thread_id": ctx.thread_id}} if ctx.thread_id else None)
    if isinstance(result, str) and result.startswith("Error"):
        raise RuntimeError(result)
    return str(result)


async def agent_task(ctx: JobContext) -> dict[str, Any]:
    agent = str(ctx.payload.get("agent") or "")
    kind = str(ctx.payload.get("kind") or "subagent")
    task = str(ctx.payload.get("task") or "")
    if not agent or not task:
        raise RuntimeError("agents.task needs 'agent' and 'task'")
    result = await (_run_acp(ctx, agent, task) if kind == "acp" else _run_subagent(ctx, agent, task))
    artifact = _write_artifact(ctx, agent, task, result)
    await ctx.progress(100, "done")
    return {"agent": agent, "kind": kind, "result": result[:20000], "artifact": artifact}


def register(registry: JobRegistry) -> None:
    registry.register(AGENT_TASK_JOB, agent_task)
