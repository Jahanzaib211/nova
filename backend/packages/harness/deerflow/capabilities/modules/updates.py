"""Updates: what version of each moving part is running."""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from pydantic import BaseModel

from deerflow.capabilities.modules._common import Empty
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


async def _version_of(binary: str, *args: str) -> str | None:
    """``<binary> --version`` through the Execution Kernel (the one sanctioned
    process-spawning site), bounded to 10 s."""
    path = shutil.which(binary)
    if not path:
        return None
    try:
        from deerflow.execution import ExecutionClass, ExecutionKernel, ExecutionRequest, ResourceLimits

        result = await ExecutionKernel().execute(ExecutionRequest(argv=(path, *(args or ("--version",))), execution_class=ExecutionClass.SHELL, limits=ResourceLimits(timeout=10), intent=f"{binary} version for Settings › Updates"))
        out = (result.stdout or result.stderr or "").strip()
        return out.splitlines()[0][:120] if out else (result.error or "")
    except Exception as exc:
        return f"error: {type(exc).__name__}"


class VersionsOut(BaseModel):
    components: dict[str, Any]


async def _versions(ctx: OpContext, inp: Empty) -> VersionsOut:
    from deerflow.config.app_config import get_app_config

    cfg = get_app_config()
    claude, node, openclaw = await asyncio.gather(_version_of("claude"), _version_of("node"), _version_of("openclaw"))
    return VersionsOut(
        components={
            "config_version": getattr(cfg, "config_version", None),
            "git_sha": os.environ.get("NOVA_GIT_SHA") or os.environ.get("GIT_SHA"),
            "image": os.environ.get("NOVA_IMAGE") or os.environ.get("IMAGE_TAG"),
            "claude_cli": claude,
            "openclaw": openclaw,
            "node": node,
            "acp_adapter": next((a.args[1] for a in (getattr(cfg, "acp_agents", {}) or {}).values() if getattr(a, "args", None) and len(a.args) > 1 and "claude-agent-acp" in a.args[1]), None),
        }
    )


async def _status() -> ModuleStatus:
    return ModuleStatus(configured=True, healthy=True, detail="versions readable")


MODULE = CapabilityModule(
    id="updates",
    title="Updates",
    description="Versions of the gateway, adapters and CLIs this deployment runs.",
    status=_status,
    operations=[
        Operation(name="updates.versions", kind="read", input=Empty, output=VersionsOut, handler=_versions, description="Version of each component (config, git, Claude CLI, OpenClaw, Node, ACP adapter)."),
    ],
)
