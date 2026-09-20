"""ACP agents (Claude Code, OpenClaw): configuration and permission policy."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from deerflow.capabilities.modules._common import Empty, Items
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


def _agents() -> dict[str, Any]:
    from deerflow.config.acp_config import get_acp_agents

    return get_acp_agents() or {}


def _describe(name: str, cfg: Any) -> dict[str, Any]:
    import shutil

    binary = getattr(cfg, "command", None)
    command = [binary, *(getattr(cfg, "args", []) or [])] if binary else []
    policy = getattr(cfg, "permission_policy", None)
    return {
        "name": name,
        "command": command,
        "binary_on_path": bool(binary and shutil.which(binary)),
        "auto_approve_permissions": bool(getattr(cfg, "auto_approve_permissions", False)),
        "permission_policy": {
            "allow_kinds": list(getattr(policy, "allow_kinds", []) or []),
            "deny_kinds": list(getattr(policy, "deny_kinds", []) or []),
        },
        "description": getattr(cfg, "description", ""),
        "model": getattr(cfg, "model", None),
    }


async def _list(ctx: OpContext, inp: Empty) -> Items:
    items = [_describe(n, c) for n, c in sorted(_agents().items())]
    return Items(items=items, total=len(items))


async def _status() -> ModuleStatus:
    agents = _agents()
    if not agents:
        return ModuleStatus(configured=False, healthy=False, detail="no acp_agents in config.yaml (NOVA_ACP_AGENTS=1 to mount the overlays)")
    described = [_describe(n, c) for n, c in agents.items()]
    missing = [d["name"] for d in described if not d["binary_on_path"]]
    return ModuleStatus(configured=True, healthy=not missing, detail="all adapters on PATH" if not missing else f"binary missing for: {', '.join(missing)}")


MODULE = CapabilityModule(
    id="acp",
    title="ACP agents",
    flag="acp_agents",
    config_key="acp_agents",
    description="External agents reachable over the Agent Client Protocol and the permission policy applied to each.",
    status=_status,
    operations=[
        Operation(name="acp.agents", kind="read", input=Empty, output=Items, handler=_list, description="Configured ACP agents, whether their adapter binary is present, and their permission policy."),
    ],
)
