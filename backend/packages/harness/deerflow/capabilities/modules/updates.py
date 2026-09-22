"""Updates: what version of each moving part is running."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from deerflow.capabilities.modules._common import Empty
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


def _locate(binary: str) -> list[str] | None:
    """argv for a CLI: on PATH, or where the gateway container has it —
    Claude Code as the ACP adapter's bundled ``cli.js`` (warmed by npx into
    ~/.npm/_npx), OpenClaw as the package mounted at /opt/openclaw."""
    path = shutil.which(binary)
    if path:
        return [path]
    if binary == "claude":
        home = Path(os.environ.get("HOME") or Path.home())
        node = shutil.which("node")
        hits = sorted(home.glob(".npm/_npx/*/node_modules/@anthropic-ai/claude-agent-sdk/cli.js"))
        if node and hits:
            return [node, str(hits[-1])]
    if binary == "openclaw":
        node = shutil.which("node")
        mjs = Path(os.environ.get("NOVA_OPENCLAW_PACKAGE") or "/opt/openclaw") / "openclaw.mjs"
        if node and mjs.is_file():
            return [node, str(mjs)]
    return None


def _git_sha() -> str | None:
    """HEAD of the bind-mounted repo (the container is not a git checkout
    of its own; the repo root is two levels above ``backend``)."""
    for root in (Path(__file__).resolve().parents[5], Path("/app")):
        head = root / ".git" / "HEAD"
        try:
            ref = head.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if ref.startswith("ref: "):
            try:
                return (root / ".git" / ref[5:]).read_text(encoding="utf-8").strip()[:12]
            except OSError:
                continue
        return ref[:12]
    return None


async def _version_of(binary: str, *args: str) -> str | None:
    """``<binary> --version`` through the Execution Kernel (the one sanctioned
    process-spawning site), bounded to 10 s."""
    argv = _locate(binary)
    if not argv:
        return None
    try:
        from deerflow.execution import ExecutionClass, ExecutionKernel, ExecutionRequest, ResourceLimits

        result = await ExecutionKernel().execute(ExecutionRequest(argv=(*argv, *(args or ("--version",))), execution_class=ExecutionClass.SHELL, limits=ResourceLimits(timeout=10), intent=f"{binary} version for Settings › Updates"))
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
            "git_sha": os.environ.get("NOVA_GIT_SHA") or os.environ.get("GIT_SHA") or _git_sha(),
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
