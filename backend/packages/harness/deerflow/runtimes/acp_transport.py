"""One ACP prompt, start to finish: spawn the adapter, initialize, open a
session (with MCP servers), send the prompt, stream text/status back.

Shared by the ``invoke_acp_agent`` tool (a subtask handed to an agent) and
the runtime dispatch middleware (a whole chat turn run by the agent).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any

from deerflow.config.acp_config import ACPAgentConfig
from deerflow.runtimes.types import PermissionPreset

logger = logging.getLogger(__name__)

#: ``(acp_session_id, text)`` — the session id is the agent's, surfaced in ``acp_update``.
TextSink = Callable[[str, str], None]


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


def resolve_env(agent_cfg: ACPAgentConfig) -> dict[str, str] | None:
    if not agent_cfg.env:
        return None
    return {k: (os.environ.get(v[1:], "") if v.startswith("$") else v) for k, v in agent_cfg.env.items()}


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
                    chunks.append(update.content.text)
                    on_text(session_id, update.content.text)
                    return
                title = getattr(update, "title", None)
                if title:
                    on_status(session_id, f"{getattr(update, 'kind', 'tool')}: {title}")
            except Exception:
                logger.debug("acp session_update handling failed", exc_info=True)

        async def request_permission(self, options, session_id: str, tool_call, **kwargs):  # type: ignore[override]
            kind = getattr(tool_call, "kind", None)
            response = build_permission_response(options, auto_approve=permission.auto_approve, policy=permission.policy, kind=kind)
            title = getattr(tool_call, "title", None) or getattr(tool_call, "tool_call_id", "")
            verdict = "approved" if response.outcome.outcome == "selected" else "denied"
            on_status(session_id, f"permission {verdict}: {kind or 'tool'} — {title}")
            return response

    async def _run() -> str:
        async with spawn_agent_process(_Client(), agent_cfg.command, *(agent_cfg.args or []), env=resolve_env(agent_cfg), cwd=cwd) as (conn, _proc):
            await conn.initialize(protocol_version=PROTOCOL_VERSION, client_capabilities=ClientCapabilities(), client_info=Implementation(name="nova", title="Nova", version="0.1.0"))
            kwargs: dict[str, Any] = {"cwd": cwd, "mcp_servers": mcp_servers}
            if model or agent_cfg.model:
                kwargs["model"] = model or agent_cfg.model
            session = await conn.new_session(**kwargs)
            await conn.prompt(session_id=session.session_id, prompt=[text_block(prompt)])
        return "".join(chunks)

    if timeout:
        return await asyncio.wait_for(_run(), timeout=timeout)
    return await _run()
