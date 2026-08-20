"""Runtime capabilities endpoint — skills, tools, hooks, subagents, circuit states.

Powers the ``<RuntimeCapabilitiesBar />`` UI component. Surfaces, in a
single authenticated call:

  - loaded skills (name + enabled state + category)
  - builtin tools (name + a one-line description if available)
  - currently registered hooks (session-level, e.g. middlewares active)
  - available subagents (name + description)
  - circuit-breaker snapshot (per-thread open/closed state)
  - browser subsystem health (status + last_check_at)

The endpoint is **read-only**, **fast** (no I/O), and **additive**.
Falls back to empty lists if any optional subsystem isn't loaded so the
UI can always render something.

Auth: same as other agent-facing routes — public paths in
``auth_middleware.py`` already cover ``/api/health``; this endpoint
reuses the standard session-cookie flow.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.gateway.deps import get_config
from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["runtime"])


# ---------- response models ----------


class SkillSummary(BaseModel):
    name: str
    description: str
    category: str  # "public" | "custom"
    enabled: bool


class ToolSummary(BaseModel):
    name: str
    description: str = ""


class HookSummary(BaseModel):
    name: str
    kind: str  # "middleware" | "event" | "callback"


class SubagentSummary(BaseModel):
    name: str
    description: str = ""


class CircuitEntry(BaseModel):
    thread_id: str
    state: str  # "closed" | "open" | "half_open"


class IGINOSummary(BaseModel):
    enabled: bool = False
    tor_enabled: bool = False
    tor_available: bool = False
    searxng_healthy: bool = False
    circuit_states: dict[str, str] = Field(default_factory=dict)
    cache_stats: dict[str, Any] = Field(default_factory=dict)
    audit_stats: dict[str, Any] = Field(default_factory=dict)


class CapabilitiesResponse(BaseModel):
    skills: list[SkillSummary] = Field(default_factory=list)
    tools: list[ToolSummary] = Field(default_factory=list)
    hooks: list[HookSummary] = Field(default_factory=list)
    subagents: list[SubagentSummary] = Field(default_factory=list)
    circuits: list[CircuitEntry] = Field(default_factory=list)
    igino: IGINOSummary = Field(default_factory=IGINOSummary)
    server: dict[str, Any] = Field(default_factory=dict)


# ---------- safe importers (never raise) ----------


def _safe_skills(config: AppConfig) -> list[SkillSummary]:
    """List loaded skills via the same path as /api/skills."""
    try:
        from deerflow.skills.storage import get_or_new_skill_storage

        storage = get_or_new_skill_storage(app_config=config)
        skills = storage.load_skills(enabled_only=False) or []
    except Exception as e:
        logger.debug("capabilities: skill discovery failed: %s", e)
        return []

    out: list[SkillSummary] = []
    for skill in skills:
        try:
            out.append(
                SkillSummary(
                    name=getattr(skill, "name", "") or "",
                    description=getattr(skill, "description", "") or "",
                    category=str(getattr(skill, "category", "public") or "public"),
                    enabled=bool(getattr(skill, "enabled", True)),
                )
            )
        except Exception as e:
            logger.debug("capabilities: skill %r parse failed: %s", getattr(skill, "name", "?"), e)
    return out


def _safe_tools(config: AppConfig) -> list[ToolSummary]:
    """List the tools an agent actually binds, with one-line descriptions.

    Built through ``get_available_tools()`` — the same call the lead agent uses
    — so this cannot drift from what the model is really given.

    It used to iterate ``BUILTIN_TOOLS`` alone, despite this docstring claiming
    "builtin + configured". That silently omitted the whole config half:
    ``bash``, ``ls``, ``read_file``, ``glob``, ``grep``, ``write_file``,
    ``str_replace``, the web tools, the trading group, plus ``task`` and
    ``view_image``. On a live gateway it reported 25 where the agent had ~38, so
    the panel an operator reads to know what Nova can do was under-reporting by
    roughly a third — and the missing entries were the ones people most want to
    confirm (can it run a shell? can it read my files?).

    ``subagent_enabled=True`` matches the gateway's own runs, where the ``task``
    tool is bound. MCP tools are excluded: they are reported separately, and
    resolving them here would make a UI poll wait on remote servers.
    """
    try:
        from deerflow.agents.manifest import _TOOL_PURPOSE_OVERRIDES
        from deerflow.tools.tools import get_available_tools

        tools = get_available_tools(
            include_mcp=False,
            subagent_enabled=True,
            app_config=config,
        )

        out: list[ToolSummary] = []
        seen: set[str] = set()
        for tool in tools:
            name = getattr(tool, "name", "") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            desc = _TOOL_PURPOSE_OVERRIDES.get(name, "") or ((getattr(tool, "description", "") or "").splitlines()[0] if getattr(tool, "description", None) else "")
            out.append(ToolSummary(name=name, description=desc[:140]))
        return sorted(out, key=lambda t: t.name)
    except Exception as e:
        # Never fail the capabilities poll over tool discovery — a config tool
        # with a bad `use:` path would otherwise blank the whole panel.
        logger.debug("capabilities: tool discovery failed: %s", e)
        return []


def _middleware_hook_name(cls_name: str) -> str:
    """``ThreadDataMiddleware`` -> ``thread_data``."""
    base = cls_name.removesuffix("Middleware")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()


# Served only if building the real chain fails (e.g. config half-loaded).
_FALLBACK_HOOK_NAMES = (
    "thread_data",
    "uploads",
    "title",
    "observe_adjust",
    "llm_error_handling",
    "preflight_quota",
    "strip_error_fallback",
    "loop_detection",
    "subagent_limit",
    "reflect_fix_budget",
    "skill_activation",
)


def _safe_hooks(config: AppConfig) -> list[HookSummary]:
    """List active middlewares by building the real lead-agent chain.

    Previously a hardcoded 11-name list that silently drifted from the
    actual chain (~19 middlewares) — the UI showed fiction. Constructors
    are cheap (`lazy_init=True` path); heavy resources initialize on first
    agent run, not here. Pinned against the real chain by
    ``test_hooks_reflect_real_middleware_chain``.
    """
    try:
        from deerflow.agents.lead_agent.agent import build_middlewares

        chain = build_middlewares({"configurable": {}}, None, app_config=config)
        names: list[str] = []
        for m in chain:
            name = _middleware_hook_name(type(m).__name__)
            if name not in names:
                names.append(name)
        if names:
            return [HookSummary(name=n, kind="middleware") for n in names]
    except Exception as e:
        logger.debug("capabilities: middleware chain build failed, serving static fallback: %s", e)
    return [HookSummary(name=n, kind="middleware") for n in _FALLBACK_HOOK_NAMES]


def _safe_subagents(config: AppConfig) -> list[SubagentSummary]:
    """List registered subagents via the same registry as the lead prompt."""
    try:
        from deerflow.subagents import get_available_subagent_names

        names = get_available_subagent_names()
    except Exception as e:
        logger.debug("capabilities: subagent registry failed: %s", e)
        return []

    out: list[SubagentSummary] = []
    try:
        from deerflow.subagents.registry import get_subagent_config

        for name in names:
            cfg = get_subagent_config(name)
            desc = (getattr(cfg, "description", "") or "") if cfg else ""
            out.append(SubagentSummary(name=name, description=desc[:140]))
    except Exception as e:
        # Fall back to names-only.
        logger.debug("capabilities: subagent config lookup failed: %s", e)
        for name in names:
            out.append(SubagentSummary(name=name, description=""))
    return out


def _safe_circuits(config: AppConfig) -> list[CircuitEntry]:
    """Read-only snapshot of per-thread circuit-breaker state."""
    try:
        from deerflow.sandbox import browser_circuit_breaker as cb

        snap = cb.snapshot()
        return [CircuitEntry(thread_id=tid, state=state) for tid, state in snap.items()]
    except Exception as e:
        logger.debug("capabilities: circuit snapshot failed: %s", e)
        return []


def _safe_server_info() -> dict[str, Any]:
    """Lightweight server-side metadata for the UI footer."""
    info: dict[str, Any] = {
        "process": "deer-flow-gateway",
        "version": os.environ.get("DEERFLOW_VERSION", "dev"),
        "pid": os.getpid(),
    }
    try:
        from deerflow.subagents.config import MAX_CONCURRENT_SUBAGENTS

        # The subagents pill shows "N types"; the tooltip needs the run
        # concurrency too, or users read "2" as the number of running tasks.
        info["max_concurrent_subagents"] = MAX_CONCURRENT_SUBAGENTS
    except Exception:  # pragma: no cover - constant import cannot realistically fail
        pass
    return info


async def _safe_igino() -> IGINOSummary:
    """Read-only iGIN0 status snapshot."""
    try:
        enabled = os.environ.get("DEERFLOW_IGINO_ENABLED", "false").lower() in ("true", "1", "yes")
        tor_enabled = os.environ.get("DEERFLOW_IGINO_TOR_ENABLED", "false").lower() in ("true", "1", "yes")

        tor_available = False
        if tor_enabled:
            try:
                from deerflow.community.searxng.tor import get_tor_proxy

                tor_available = get_tor_proxy().is_available()
            except Exception:
                pass

        searxng_healthy = False
        try:
            from deerflow.community.searxng.searxng_client import SearxngClient

            client = SearxngClient()
            # Actually probe SearXNG with a short GET to /healthz instead
            # of always reporting True (Batch 2C audit fix). The probe is
            # best-effort: timeout, connection refused, and non-2xx all
            # surface as searxng_healthy=False so the UI reflects reality.
            searxng_healthy = await client.probe_health()
        except Exception:
            # If even constructing the client fails (missing config etc.)
            # fall back to False; don't pretend it's healthy.
            searxng_healthy = False

        circuit_states: dict[str, str] = {}
        try:
            from deerflow.community.searxng.search_cache import get_search_cache

            cache_stats = get_search_cache().stats
        except Exception:
            cache_stats = {}

        try:
            from deerflow.community.searxng.audit import get_audit_trail

            audit_stats = get_audit_trail().get_stats()
        except Exception:
            audit_stats = {}

        return IGINOSummary(
            enabled=enabled,
            tor_enabled=tor_enabled,
            tor_available=tor_available,
            searxng_healthy=searxng_healthy,
            circuit_states=circuit_states,
            cache_stats=cache_stats,
            audit_stats=audit_stats,
        )
    except Exception as e:
        logger.debug("capabilities: igino snapshot failed: %s", e)
        return IGINOSummary()


# ---------- endpoint ----------


@router.get(
    "/runtime/capabilities",
    response_model=CapabilitiesResponse,
    summary="List loaded skills, tools, hooks, subagents, and circuit states",
)
async def get_runtime_capabilities(
    config: AppConfig = Depends(get_config),
) -> CapabilitiesResponse:
    """Returns the agent's currently loaded skill/tool/hook/subagent set
    plus a per-thread circuit-breaker snapshot. Read-only, fast (no I/O).
    """
    return CapabilitiesResponse(
        skills=_safe_skills(config),
        tools=_safe_tools(config),
        hooks=_safe_hooks(config),
        subagents=_safe_subagents(config),
        circuits=_safe_circuits(config),
        igino=await _safe_igino(),
        server=_safe_server_info(),
    )
