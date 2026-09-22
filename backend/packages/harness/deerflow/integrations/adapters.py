"""Adapters for things Nova already knows about without a URL: MCP servers,
skills and ACP agents. They read state the gateway already holds — the MCP
tool cache, the skill storage, the ACP config — and never connect anywhere
on the request path (an MCP stdio connect can take seconds and spawn a
process; that belongs to the cache loader, not a health card).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable

from deerflow.integrations.base import now_iso
from deerflow.integrations.health import HealthResult, IntegrationKind, IntegrationStatus

Adapter = Callable[[], Iterable[HealthResult]]


def _mcp_servers() -> dict[str, dict]:
    from deerflow.config.extensions_config import ExtensionsConfig

    cfg = ExtensionsConfig.from_file()
    return {name: {"enabled": bool(server.enabled), "type": server.type} for name, server in cfg.mcp_servers.items()}


def _mcp_tool_counts() -> tuple[dict[str, int], bool]:
    """Tools per server from the cache; ``initialised`` False until startup loaded it."""
    from deerflow.mcp import cache

    tools = getattr(cache, "_mcp_tools_cache", None)
    initialised = bool(getattr(cache, "_cache_initialized", False))
    counts: dict[str, int] = {}
    for tool in tools or []:
        name = getattr(tool, "name", "")
        for server in _mcp_servers():
            if name.startswith(f"{server}_"):
                counts[server] = counts.get(server, 0) + 1
    return counts, initialised


def _skills() -> list[tuple[str, bool]]:
    from deerflow.config.app_config import get_app_config
    from deerflow.skills.storage import get_or_new_skill_storage

    storage = get_or_new_skill_storage(app_config=get_app_config())
    return [(skill.name, bool(getattr(skill, "enabled", True))) for skill in storage.load_skills(enabled_only=False) or []]


def _acp_agents() -> dict:
    from deerflow.config.acp_config import get_acp_agents

    return dict(get_acp_agents())


def _item(id: str, kind: IntegrationKind, display: str, status: IntegrationStatus, detail: str | None, capabilities: list[str] | None = None) -> HealthResult:
    return HealthResult(id=id, kind=kind, display_name=display, status=status, endpoint=None, latency_ms=None, checked_at=now_iso(), detail=detail, capabilities=capabilities or [])


def mcp_adapter() -> list[HealthResult]:
    servers = _mcp_servers()
    counts, initialised = _mcp_tool_counts()
    out: list[HealthResult] = []
    for name, meta in sorted(servers.items()):
        if not meta.get("enabled"):
            out.append(_item(f"mcp:{name}", IntegrationKind.MCP_SERVER, name, IntegrationStatus.DISABLED, "disabled", [meta.get("type", "stdio")]))
        elif not initialised:
            out.append(_item(f"mcp:{name}", IntegrationKind.MCP_SERVER, name, IntegrationStatus.UNKNOWN, "tool cache not loaded yet", [meta.get("type", "stdio")]))
        elif counts.get(name, 0) > 0:
            out.append(_item(f"mcp:{name}", IntegrationKind.MCP_SERVER, name, IntegrationStatus.HEALTHY, f"{counts[name]} tools", [meta.get("type", "stdio")]))
        else:
            out.append(_item(f"mcp:{name}", IntegrationKind.MCP_SERVER, name, IntegrationStatus.DEGRADED, "enabled but loaded 0 tools", [meta.get("type", "stdio")]))
    return out


def skills_adapter() -> list[HealthResult]:
    return [_item(f"skill:{name}", IntegrationKind.SKILL, name, IntegrationStatus.HEALTHY if enabled else IntegrationStatus.DISABLED, "loaded" if enabled else "disabled") for name, enabled in sorted(_skills())]


def acp_adapter() -> list[HealthResult]:
    out: list[HealthResult] = []
    for name, agent in sorted(_acp_agents().items()):
        binary = (agent.command or "").split()[0] if agent.command else ""
        found = bool(binary) and shutil.which(binary) is not None
        out.append(
            _item(
                f"acp:{name}",
                IntegrationKind.ACP_AGENT,
                name,
                IntegrationStatus.HEALTHY if found else IntegrationStatus.DOWN,
                f"{binary} on PATH" if found else f"{binary or '<no command>'} not found on PATH",
                ["acp"],
            )
        )
    return out


def default_adapters() -> list[Adapter]:
    return [mcp_adapter, skills_adapter, acp_adapter]
