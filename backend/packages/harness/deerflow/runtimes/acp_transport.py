"""One ACP prompt, start to finish: spawn the adapter, initialize, open a
session (with MCP servers), send the prompt, stream text/status back.

Shared by the ``invoke_acp_agent`` tool (a subtask handed to an agent) and
the runtime dispatch middleware (a whole chat turn run by the agent).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deerflow.config.acp_config import ACPAgentConfig
from deerflow.runtimes.types import PermissionPreset

logger = logging.getLogger(__name__)

#: ``(acp_session_id, text)`` — the session id is the agent's, surfaced in ``acp_update``.
TextSink = Callable[[str, str], None]

#: Max bytes in one ACP JSON-RPC frame.
#:
#: asyncio's StreamReader defaults to 64 KiB, and a single ACP frame routinely
#: exceeds that: ``new_session`` carries the full MCP tool list (Nova alone
#: contributes 19 capability tools with their JSON schemas), and a tool *result*
#: carrying a file or a command's output is unbounded in practice. Past the
#: limit ``readline()`` raises ``ValueError: Separator is found, but chunk is
#: longer than limit`` and the whole turn dies — which is exactly how the
#: ``claude_code`` runtime failed on 2026-09-20. Applies to stdout and stderr
#: both, so it also covers the stderr drain below.
_DEFAULT_STREAM_LIMIT = 8 * 1024 * 1024


def _stream_limit() -> int:
    """Parse the override, falling back rather than failing the import.

    This runs at module scope, and the module is imported on the gateway's
    startup path: a typo'd or empty ``NOVA_ACP_STREAM_LIMIT`` raising here
    would turn one bad env var into a gateway that cannot boot at all. A
    non-positive value is equally unusable, so it is refused the same way.
    """
    raw = os.environ.get("NOVA_ACP_STREAM_LIMIT", "").strip()
    if not raw:
        return _DEFAULT_STREAM_LIMIT
    try:
        value = int(raw)
    except ValueError:
        logger.warning("NOVA_ACP_STREAM_LIMIT=%r is not an integer — using %d", raw, _DEFAULT_STREAM_LIMIT)
        return _DEFAULT_STREAM_LIMIT
    if value <= 0:
        logger.warning("NOVA_ACP_STREAM_LIMIT=%d must be positive — using %d", value, _DEFAULT_STREAM_LIMIT)
        return _DEFAULT_STREAM_LIMIT
    return value


ACP_STREAM_LIMIT = _stream_limit()


class ACPFrameTooLarge(RuntimeError):
    """One ACP frame exceeded :data:`ACP_STREAM_LIMIT`.

    Raised in place of asyncio's bare ``ValueError`` so the failure names the
    limit and the knob instead of reading as a generic parse error.
    """


def build_acp_mcp_servers() -> list[dict[str, Any]]:
    """Nova's enabled MCP *client* servers in ACP ``new_session`` wire format."""
    from deerflow.config.extensions_config import ExtensionsConfig

    enabled = ExtensionsConfig.from_file().get_enabled_mcp_servers()
    out: list[dict[str, Any]] = []
    for name, cfg in enabled.items():
        transport = cfg.type or "stdio"
        payload: dict[str, Any] = {"name": name, "type": transport}
        if transport == "stdio":
            if not cfg.command:
                raise ValueError(f"MCP server '{name}' with stdio transport requires 'command' field")
            payload["command"] = cfg.command
            payload["args"] = cfg.args
            payload["env"] = [{"name": k, "value": v} for k, v in cfg.env.items()]
        elif transport in ("http", "sse"):
            if not cfg.url:
                raise ValueError(f"MCP server '{name}' with {transport} transport requires 'url' field")
            payload["url"] = cfg.url
            payload["headers"] = [{"name": k, "value": v} for k, v in cfg.headers.items()]
        else:
            raise ValueError(f"MCP server '{name}' has unsupported transport type: {transport}")
        out.append(payload)
    return out


def build_permission_response(options: Any, *, auto_approve: bool, policy: Any | None = None, kind: str | None = None):
    """Build an ACP permission response.

    Approval is decided by the agent's ``permission_policy`` for the tool
    call's ``kind`` (``deny_kinds`` always wins, ``allow_kinds`` approves,
    ``auto_approve`` approves everything else); an approved request selects
    the first ``allow_once`` (preferred) or ``allow_always`` option. Anything
    else cancels — the agent must then work without that permission.
    """
    from acp import RequestPermissionResponse
    from acp.schema import AllowedOutcome, DeniedOutcome

    approved = policy.decide(kind, auto_approve=auto_approve) if policy is not None else auto_approve
    if approved:
        for preferred_kind in ("allow_once", "allow_always"):
            for option in options:
                if getattr(option, "kind", None) != preferred_kind:
                    continue
                option_id = getattr(option, "option_id", None)
                if option_id is None:
                    option_id = getattr(option, "optionId", None)
                if option_id is None:
                    continue
                return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", optionId=option_id))
    return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


NOVA_TOOL_PREFIX = "mcp__nova__"


def nova_tool_decision(title: str | None, mode: str, kind_of: Callable[[str], str | None]) -> bool | None:
    """Decide a call to one of Nova's own MCP tools by the *operation's* kind.

    Claude Code reports every MCP tool call with ACP kind ``other``, so the
    per-kind ACP policy alone would deny Nova's tools in every mode but
    ``full``. ``mcp__nova__jobs__list`` maps to operation ``jobs.list`` whose
    registry kind decides: ``read`` is allowed in every mode; ``write`` and
    ``execute`` in ``standard`` and ``full``; ``secret``/``admin`` never
    (they are not served on the MCP server anyway). ``None`` means "not a
    known Nova tool — apply the ACP policy".
    """
    if not title or not title.startswith(NOVA_TOOL_PREFIX):
        return None
    op_name = title[len(NOVA_TOOL_PREFIX) :].replace("__", ".", 1)
    kind = kind_of(op_name)
    if kind is None:
        return None
    if kind == "read":
        return True
    if kind in ("write", "execute"):
        return mode in ("standard", "full")
    return False


def _registry_kind(op_name: str) -> str | None:
    try:
        from deerflow.capabilities import get_registry

        return get_registry().get(op_name).kind
    except Exception:
        return None


def resolve_env(agent_cfg: ACPAgentConfig) -> dict[str, str] | None:
    if not agent_cfg.env:
        return None
    return {k: (os.environ.get(v[1:], "") if v.startswith("$") else v) for k, v in agent_cfg.env.items()}


def _descendants(pid: int) -> set[int]:
    """Every PID below *pid*, read from ``/proc`` (the gateway runs in the container).

    Needed because the tracked process is usually ``npx``, a launcher: the real
    adapter is its child, so killing only the tracked PID leaves the adapter
    behind.
    """
    found: set[int] = set()
    stack = [pid]
    while stack:
        cur = stack.pop()
        try:
            kids = Path(f"/proc/{cur}/task/{cur}/children").read_text().split()
        except OSError:
            continue
        for raw in kids:
            try:
                kid = int(raw)
            except ValueError:
                continue
            if kid not in found:
                found.add(kid)
                stack.append(kid)
    return found


def _reap_adapter(proc: Any, descendants: set[int] | None = None) -> None:
    """Kill the ACP adapter *and its children* if they outlived the turn.

    Two independent leaks, both observed live on 2026-09-21 (five orphaned
    ``claude-agent-acp`` node processes, the oldest 24 h old, against six
    sessions ever):

    1. **Cancellation.** The transport's teardown is correct but every step of
       it *awaits*. Inside an already-cancelled task — a turn that hit its
       timeout, or a client that disconnected mid-stream — those awaits raise
       ``CancelledError`` immediately, so terminate/kill never run.
    2. **The launcher.** ``npx`` is what asyncio tracks; the adapter is its
       child. The transport kills ``npx``, the child is orphaned onto the
       container's PID 1 (which *is* the gateway's uvicorn), and nothing ever
       reaps it. ``returncode`` is set, so the tracked process looks clean.

    ``kill``/``os.kill`` are synchronous, so both still land while the task
    unwinds.
    """
    # Cleanup must never be the thing that fails a turn, so every step is
    # suppressed independently.
    with contextlib.suppress(Exception):
        if proc is not None and proc.returncode is None:
            proc.kill()
            logger.warning("acp adapter pid=%s outlived its turn — killed", proc.pid)

    for pid in sorted(descendants or ()):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGKILL)
            logger.warning("acp adapter child pid=%s orphaned by its launcher — killed", pid)


async def run_acp_prompt(
    agent_cfg: ACPAgentConfig,
    prompt: str,
    *,
    cwd: str,
    mcp_servers: list[dict[str, Any]],
    permission: PermissionPreset,
    on_text: TextSink,
    on_status: TextSink,
    model: str | None = None,
    timeout: float | None = None,
) -> str:
    """Run one prompt and return the agent's full text. Raises on transport failure."""
    from acp import PROTOCOL_VERSION, Client, spawn_agent_process, text_block
    from acp.schema import ClientCapabilities, Implementation, TextContentBlock

    chunks: list[str] = []

    class _Client(Client):
        async def session_update(self, session_id: str, update, **kwargs) -> None:  # type: ignore[override]
            try:
                if hasattr(update, "content") and isinstance(update.content, TextContentBlock):
                    # ACP streams three kinds of text on the same field. Only
                    # ``agent_message_chunk`` is the answer: a thought chunk is
                    # the model reasoning aloud, and a user chunk is our own
                    # prompt echoed back. Appending all three is why a reply
                    # once read "The user wants me to call a Nova MCP tool...
                    # Let me search for relevant tools.**28 modules.**" — the
                    # reasoning was concatenated onto the answer.
                    update_kind = str(getattr(update, "session_update", "") or "")
                    text = update.content.text
                    if update_kind == "agent_thought_chunk":
                        on_status(session_id, f"thinking: {text}")
                        return
                    if update_kind == "user_message_chunk":
                        return
                    chunks.append(text)
                    on_text(session_id, text)
                    return
                title = getattr(update, "title", None)
                if title:
                    on_status(session_id, f"{getattr(update, 'kind', 'tool')}: {title}")
            except Exception:
                logger.debug("acp session_update handling failed", exc_info=True)

        async def request_permission(self, options, session_id: str, tool_call, **kwargs):  # type: ignore[override]
            kind = getattr(tool_call, "kind", None)
            title = getattr(tool_call, "title", None) or getattr(tool_call, "tool_call_id", "")
            nova = nova_tool_decision(getattr(tool_call, "title", None), permission.mode, _registry_kind)
            if nova is None:
                response = build_permission_response(options, auto_approve=permission.auto_approve, policy=permission.policy, kind=kind)
            else:
                response = build_permission_response(options, auto_approve=nova, policy=None, kind=kind)
            verdict = "approved" if response.outcome.outcome == "selected" else "denied"
            on_status(session_id, f"permission {verdict}: {kind or 'tool'} — {title}")
            return response

    async def _drain_stderr(proc: Any) -> None:
        """The ACP lib pipes the adapter's stderr and never reads it: a chatty
        adapter would block on a full pipe, and its errors would vanish. Log
        it (bounded) instead."""
        stream = getattr(proc, "stderr", None)
        if stream is None:
            return
        kept = 0
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                if kept < 200:
                    logger.info("acp[%s] %s", agent_cfg.command, line.decode(errors="replace").rstrip()[:400])
                    kept += 1
        except Exception:
            pass

    async def _run() -> str:
        proc_ref: Any = None
        kids: set[int] = set()
        try:
            async with spawn_agent_process(
                _Client(),
                agent_cfg.command,
                *(agent_cfg.args or []),
                env=resolve_env(agent_cfg),
                cwd=cwd,
                transport_kwargs={"limit": ACP_STREAM_LIMIT},
            ) as (conn, proc):
                proc_ref = proc
                drain = asyncio.create_task(_drain_stderr(proc))
                try:
                    await conn.initialize(protocol_version=PROTOCOL_VERSION, client_capabilities=ClientCapabilities(), client_info=Implementation(name="nova", title="Nova", version="0.1.0"))
                    kwargs: dict[str, Any] = {"cwd": cwd, "mcp_servers": mcp_servers}
                    logger.info("acp session for %s: cwd=%s mcp_servers=%s", agent_cfg.command, cwd, [f"{m.get('name')}({m.get('type', 'stdio')})" for m in mcp_servers])
                    if model or agent_cfg.model:
                        kwargs["model"] = model or agent_cfg.model
                    session = await conn.new_session(**kwargs)
                    await conn.prompt(session_id=session.session_id, prompt=[text_block(prompt)])
                finally:
                    # Was only cancelled on the success path, so a failed
                    # initialize/prompt leaked the drain task too.
                    drain.cancel()
                    # Snapshot the tree *here*, while the launcher is still
                    # alive. Once the transport's teardown kills it the
                    # children reparent to PID 1 and are no longer findable
                    # from this process.
                    with contextlib.suppress(Exception):
                        kids = _descendants(proc.pid)
        finally:
            _reap_adapter(proc_ref, kids)
        return "".join(chunks)

    try:
        if timeout:
            return await asyncio.wait_for(_run(), timeout=timeout)
        return await _run()
    except ValueError as exc:  # asyncio StreamReader overrun
        if "chunk is longer than limit" not in str(exc):
            raise
        raise ACPFrameTooLarge(f"an ACP frame from {agent_cfg.command} exceeded {ACP_STREAM_LIMIT} bytes (raise NOVA_ACP_STREAM_LIMIT)") from exc
