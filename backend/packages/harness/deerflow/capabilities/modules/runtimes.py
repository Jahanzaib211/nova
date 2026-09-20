"""Runtimes: what can run a chat (native, Claude Code, OpenClaw), the
accounts each can use, a live probe ("Check model") and permission modes."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from deerflow.capabilities.modules._common import Empty, Items
from deerflow.capabilities.types import CapabilityModule, ModuleStatus, OpContext, Operation


def _registry():
    from deerflow.runtimes import get_runtime_registry

    return get_runtime_registry()


class DescribeOut(BaseModel):
    enabled: bool
    default: str
    runtimes: list[dict[str, Any]]
    permission_modes: list[str]
    modes: list[dict[str, Any]]


async def _list(ctx: OpContext, inp: Empty) -> DescribeOut:
    from deerflow.config.app_config import get_app_config

    rt = getattr(get_app_config(), "runtimes", None)
    d = _registry().describe()
    return DescribeOut(enabled=bool(getattr(rt, "enabled", False)), **d)


class ProbeIn(BaseModel):
    runtime: str = Field(description="Runtime id from runtimes.list (e.g. claude_code)")
    account: str | None = Field(default=None, description="Account id, or omit for automatic selection")


class ProbeOut(BaseModel):
    runtime: str
    account: str
    ok: bool
    latency_ms: int | None = None
    detail: str = ""
    checked_at: str = ""


async def _probe(ctx: OpContext, inp: ProbeIn) -> ProbeOut:
    health = await _registry().probe(inp.runtime, inp.account)
    return ProbeOut(**dataclasses.asdict(health))


class SessionsIn(BaseModel):
    runtime: str = Field(default="claude_code")
    limit: int = Field(default=50, ge=1, le=500)


async def _sessions(ctx: OpContext, inp: SessionsIn) -> Items:
    """Claude Code keeps one JSONL per session under
    ``<login dir>/projects/<encoded cwd>/``; list them newest first. Names
    and timestamps only — transcripts are never read."""
    if inp.runtime != "claude_code":
        return Items(items=[], total=0)
    reg = _registry()
    root = Path(reg._claude_login_dir) / "projects"  # noqa: SLF001 — registry owns the path
    items: list[dict[str, Any]] = []
    if root.is_dir():
        for project in sorted(root.iterdir()):
            if not project.is_dir():
                continue
            for f in project.glob("*.jsonl"):
                st = f.stat()
                items.append({"project": project.name.replace("-", "/").lstrip("/"), "session_id": f.stem, "bytes": st.st_size, "modified_at": int(st.st_mtime)})
    items.sort(key=lambda i: i["modified_at"], reverse=True)
    return Items(items=items[: inp.limit], total=len(items))


async def _status() -> ModuleStatus:
    from deerflow.config.app_config import get_app_config

    rt = getattr(get_app_config(), "runtimes", None)
    if rt is None or not getattr(rt, "enabled", False):
        return ModuleStatus(configured=False, healthy=False, detail="runtimes.enabled is false (native only)")
    reg = _registry()
    ready = [r for r in reg.ids() if r == "native" or any(a.available for a in reg.accounts_for(r))]
    return ModuleStatus(configured=True, healthy=len(ready) > 1, detail=f"ready: {', '.join(ready)}")


MODULE = CapabilityModule(
    id="runtimes",
    title="Runtimes",
    flag="runtimes",
    config_key="runtimes",
    description="What can run a chat turn — Nova's native agent, Claude Code or OpenClaw over ACP — with accounts, a live probe and permission modes.",
    status=_status,
    operations=[
        Operation(name="runtimes.list", kind="read", input=Empty, output=DescribeOut, handler=_list, description="Runtimes, the accounts each can use (presence only), the default, and permission modes."),
        Operation(name="runtimes.probe", kind="execute", input=ProbeIn, output=ProbeOut, handler=_probe, description='Round-trip one word through a runtime — the "Check model" button.'),
        Operation(name="runtimes.sessions", kind="read", input=SessionsIn, output=Items, handler=_sessions, description="Claude Code sessions on this gateway, grouped by project (names and times only)."),
    ],
)
