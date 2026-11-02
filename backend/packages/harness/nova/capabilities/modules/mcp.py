"""MCP: the servers Nova connects to as a client, and their loaded tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Empty, Items, Ok
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


def _servers() -> dict[str, Any]:
    from deerflow.config.extensions_config import get_extensions_config

    return get_extensions_config().mcp_servers


def _tool_counts() -> dict[str, int]:
    # Same counter as the integrations registry (by ``<server>_`` name
    # prefix). This used to read ``metadata["mcp_server"]``, which the MCP
    # adapter never sets, so every server reported 0 tools.
    from deerflow.mcp.cache import tool_counts_by_server

    return tool_counts_by_server(_servers())


async def _list(ctx: OpContext, inp: Empty) -> Items:
    counts = _tool_counts()
    items = [
        {
            "name": name,
            "enabled": s.enabled,
            "type": s.type,
            "command": s.command,
            "url": s.url,
            "description": s.description,
            "tool_count": counts.get(name, 0),
        }
        for name, s in sorted(_servers().items())
    ]
    return Items(items=items, total=len(items))


async def _tools(ctx: OpContext, inp: Empty) -> Items:
    from deerflow.mcp.cache import get_cached_mcp_tools, server_for_tool

    servers = list(_servers())
    items = [{"name": t.name, "description": (t.description or "")[:200], "server": server_for_tool(t.name, servers)} for t in get_cached_mcp_tools()]
    return Items(items=items, total=len(items))


class ToggleIn(BaseModel):
    name: str = Field(description="MCP server name")
    enabled: bool


async def _toggle(ctx: OpContext, inp: ToggleIn) -> Ok:
    from deerflow.config.extensions_config import set_mcp_server_enabled

    set_mcp_server_enabled(inp.name, inp.enabled)
    try:
        from deerflow.mcp.cache import reset_mcp_tools_cache

        reset_mcp_tools_cache()
    except Exception:
        pass
    return Ok(ok=True, detail=f"{inp.name} {'enabled' if inp.enabled else 'disabled'}; tools reload on next run")


async def _status() -> ModuleStatus:
    servers = _servers()
    enabled = [n for n, s in servers.items() if s.enabled]
    return ModuleStatus(configured=bool(servers), healthy=True, detail=f"{len(enabled)}/{len(servers)} server(s) enabled")


MODULE = CapabilityModule(
    id="mcp",
    title="MCP",
    config_key="extensions_config.json",
    description="Model Context Protocol servers Nova connects to and the tools they contribute.",
    status=_status,
    operations=[
        Operation(name="mcp.servers", kind="read", input=Empty, output=Items, handler=_list, description="Configured MCP servers, transport, enabled state and loaded tool count."),
        Operation(name="mcp.tools", kind="read", input=Empty, output=Items, handler=_tools, description="Tools currently loaded from MCP servers."),
        Operation(name="mcp.toggle", kind="write", input=ToggleIn, output=Ok, handler=_toggle, description="Enable or disable an MCP server.", admin_only=True),
    ],
)
