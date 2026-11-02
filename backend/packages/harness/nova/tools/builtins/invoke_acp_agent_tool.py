"""Built-in tool for invoking external ACP-compatible agents."""

import logging
import os
import shutil
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, InjectedToolArg, StructuredTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class _InvokeACPAgentInput(BaseModel):
    agent: str = Field(description="Name of the ACP agent to invoke")
    prompt: str = Field(description="The concise task prompt to send to the agent")


def _get_work_dir(thread_id: str | None) -> str:
    """Get the per-thread ACP workspace directory.

    Each thread gets an isolated workspace under
    ``{base_dir}/threads/{thread_id}/acp-workspace/`` so that concurrent
    sessions cannot read or overwrite each other's ACP agent outputs.

    Falls back to the legacy global ``{base_dir}/acp-workspace/`` when
    ``thread_id`` is not available (e.g. embedded / direct invocation).

    The directory is created automatically if it does not exist.

    Returns:
        An absolute physical filesystem path to use as the working directory.
    """
    from nova.config.paths import get_paths
    from nova.runtime.user_context import get_effective_user_id

    paths = get_paths()
    if thread_id:
        try:
            work_dir = paths.acp_workspace_dir(thread_id, user_id=get_effective_user_id())
        except ValueError:
            logger.warning("Invalid thread_id %r for ACP workspace, falling back to global", thread_id)
            work_dir = paths.base_dir / "acp-workspace"
    else:
        work_dir = paths.base_dir / "acp-workspace"

    work_dir.mkdir(parents=True, exist_ok=True)
    logger.info("ACP agent work_dir: %s", work_dir)
    return str(work_dir)


def _nova_mcp(user_id: str | None, thread_id: str | None):
    """Nova's MCP server for a delegated session: the chat runtime's factory,
    but minting a token scoped to the thread's sandbox only."""
    from nova.runtimes.session import default_nova_mcp
    from nova.runtimes.transcript import DELEGATE_ROLE

    return default_nova_mcp(user_id, thread_id, role=DELEGATE_ROLE)


def _run_journal(config: Any) -> Any | None:
    """The run's journal, from the tool's config, or None outside a run.

    The worker puts it in the runtime context (``__run_journal``); it is also
    a callback handler, so look there as a fallback (the shape ``task`` uses).
    """
    if not isinstance(config, dict):
        return None
    runtime = (config.get("configurable") or {}).get("__pregel_runtime")
    ctx = getattr(runtime, "context", None) if runtime is not None else None
    journal = ctx.get("__run_journal") if isinstance(ctx, dict) else None
    if journal is not None:
        return journal
    callbacks = config.get("callbacks")
    handlers = getattr(callbacks, "handlers", callbacks)
    if isinstance(handlers, list):
        for cb in handlers:
            if hasattr(cb, "record_external_llm_usage_records"):
                return cb
    return None


def _record_delegated_usage(config: Any, agent: str, agent_config: Any, text: Any) -> None:
    """Report a delegated ACP turn's tokens to the run's journal.

    The turn never passes through an LLM callback, so without this a whole
    delegated Claude Code task cost nothing in the run's totals. Booked under
    ``subagent:acp:<agent>`` -- the journal's subagent bucket -- because the
    lead agent did not spend these tokens, its delegate did. Must run before
    the result is returned: the ``ACPText`` (which carries the usage) becomes
    a plain ``ToolMessage`` string after that. Never raises.
    """
    try:
        usage = getattr(text, "usage", None)
        if usage is None or (getattr(usage, "total_tokens", 0) or 0) <= 0:
            return
        journal = _run_journal(config)
        if journal is None:
            return
        journal.record_external_llm_usage_records(
            [
                {
                    "source_run_id": f"acp-delegate:{agent}:{getattr(text, 'session_id', '') or uuid.uuid4().hex}",
                    "caller": f"subagent:acp:{agent}",
                    "model_name": getattr(agent_config, "model", None) or f"acp:{agent}",
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                }
            ]
        )
    except Exception:
        logger.warning("Failed to record delegated ACP usage (non-fatal)", exc_info=True)


def _build_acp_mcp_servers() -> list[dict[str, Any]]:
    """ACP ``mcpServers`` payload for ``new_session`` (shared with the runtime dispatch)."""
    from nova.runtimes.acp_transport import build_acp_mcp_servers

    return build_acp_mcp_servers()


def _stream_writer() -> Callable[[dict[str, Any]], None] | None:
    """The run's custom-event writer, or None outside a graph run.

    ``get_stream_writer()`` raises when no runnable context is active (unit
    tests, direct invocation); the tool must still work there, just silently.
    """
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:
        return None


def _build_permission_response(options: Any, *, auto_approve: bool, policy: Any | None = None, kind: str | None = None):
    """Select a permission option per policy (delegates to the shared transport)."""
    from nova.runtimes.acp_transport import build_permission_response

    return build_permission_response(options, auto_approve=auto_approve, policy=policy, kind=kind)


def _format_invocation_error(agent: str, cmd: str, exc: Exception) -> str:
    """Return a user-facing ACP invocation error with actionable remediation."""
    if not isinstance(exc, FileNotFoundError):
        return f"Error invoking ACP agent '{agent}': {exc}"

    message = f"Error invoking ACP agent '{agent}': Command '{cmd}' was not found on PATH."
    if cmd == "codex-acp" and shutil.which("codex"):
        return f"{message} The installed `codex` CLI does not speak ACP directly. Install a Codex ACP adapter (for example `npx @zed-industries/codex-acp`) or update `acp_agents.codex.command` and `args` in config.yaml."

    return f"{message} Install the agent binary or update `acp_agents.{agent}.command` in config.yaml."


def build_invoke_acp_agent_tool(agents: dict) -> BaseTool:
    """Create the ``invoke_acp_agent`` tool with a description generated from configured agents.

    The tool description includes the list of available agents so that the LLM
    knows which agents it can invoke without requiring hardcoded names.

    Args:
        agents: Mapping of agent name -> ``ACPAgentConfig``.

    Returns:
        A LangChain ``BaseTool`` ready to be included in the tool list.
    """
    agent_lines = "\n".join(f"- {name}: {cfg.description}" for name, cfg in agents.items())
    description = (
        "Invoke an external ACP-compatible agent and return its final response.\n\n"
        "Available agents:\n"
        f"{agent_lines}\n\n"
        "The agent is given this thread's sandbox through Nova's own MCP server whenever it can be attached (the normal case): "
        "it then works directly in the user's /mnt/user-data/workspace, and its changes appear in the Agent's Computer like yours do, "
        "so you may name /mnt/user-data paths in the prompt. "
        "If the sandbox cannot be attached the agent falls back to its own workspace, whose files are afterwards readable at /mnt/acp-workspace/ (read-only). "
        "Either way, give the agent a self-contained task description: it does not see this conversation."
    )

    # Capture agents in closure so the function can reference it
    _agents = dict(agents)

    async def _invoke(agent: str, prompt: str, config: Annotated[RunnableConfig, InjectedToolArg] = None) -> str:
        logger.info("Invoking ACP agent %s (prompt length: %d)", agent, len(prompt))
        logger.debug("Invoking ACP agent %s with prompt: %.200s%s", agent, prompt, "..." if len(prompt) > 200 else "")
        if agent not in _agents:
            available = ", ".join(_agents.keys())
            return f"Error: Unknown agent '{agent}'. Available: {available}"

        agent_config = _agents[agent]
        thread_id: str | None = ((config or {}).get("configurable") or {}).get("thread_id")

        try:
            from nova.runtimes.acp_transport import run_acp_prompt
        except ImportError:
            return "Error: agent-client-protocol package is not installed. Run `uv sync` to install project dependencies."

        writer = _stream_writer()

        def emit(session_id: str, kind: str, delta: str) -> None:
            # Contract: contracts/custom_events_contract.json → acp_update.
            if writer is None or not delta:
                return
            try:
                writer({"type": "acp_update", "agent": agent, "session_id": session_id, "kind": kind, "delta": delta})
            except Exception:  # never let telemetry break the invocation
                logger.debug("acp_update emit failed", exc_info=True)

        physical_cwd = _get_work_dir(thread_id)

        # The agent's own config decides permissions for a delegated subtask:
        # deny_kinds always deny, allow_kinds auto-approve, the rest follows
        # auto_approve_permissions (see backend/docs/ACP_AGENTS.md). Nova's own
        # sandbox tools are judged by operation kind, so they work in this
        # "standard" preset while the agent's private Write/Bash stay denied.
        from nova.runtime.user_context import get_effective_user_id
        from nova.runtimes.session import open_acp_session
        from nova.runtimes.transcript import DELEGATE_ROLE
        from nova.runtimes.types import PermissionPreset

        permission = PermissionPreset(mode="standard", auto_approve=agent_config.auto_approve_permissions, policy=agent_config.permission_policy, label="agent policy", description="")

        try:
            # The same session the chat runtime gets: Nova's MCP server, the
            # briefing and the Activity mirror. A delegated agent can still do
            # useful work in its own workspace, so a failed mount degrades
            # instead of failing the task.
            async with open_acp_session(
                agent=agent,
                user_id=get_effective_user_id(),
                thread_id=thread_id,
                role=DELEGATE_ROLE,
                nova_mcp=_nova_mcp,
                client_servers=_build_acp_mcp_servers,
                degrade_on_mount_error=True,
            ) as session:

                def on_status(sid: str, delta: str) -> None:
                    session.mirror(delta)
                    emit(sid, "status", delta)

                logger.info("Spawning ACP agent '%s' with command '%s' and args %s in cwd %s (nova sandbox: %s)", agent, agent_config.command, agent_config.args, physical_cwd, session.nova_mounted)
                result = await session.run(
                    run_acp_prompt,
                    agent_config,
                    prompt,
                    cwd=physical_cwd,
                    permission=permission,
                    on_text=lambda sid, d: emit(sid, "text", d),
                    on_status=on_status,
                )
            logger.info("ACP agent '%s' returned %d characters", agent, len(result))
            _record_delegated_usage(config, agent, agent_config, result)
            return result or "(no response)"
        except Exception as e:
            logger.error("ACP agent '%s' invocation failed: %s", agent, e)
            return _format_invocation_error(agent, agent_config.command, e)

    return StructuredTool.from_function(
        name="invoke_acp_agent",
        description=description,
        coroutine=_invoke,
        args_schema=_InvokeACPAgentInput,
    )
