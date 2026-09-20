"""Runtime capabilities endpoint — skills, tools, hooks, subagents, circuit states.

Powers the ``<RuntimeCapabilitiesBar />`` UI component. Surfaces, in a
single authenticated call:

  - loaded skills (name + enabled state + category)
  - builtin tools (name + a one-line description if available)
  - currently registered hooks (session-level, e.g. middlewares active)
  - available subagents (name + description)
  - circuit-breaker snapshot (per-thread open/closed state)
  - browser subsystem health (status + last_check_at)

The endpoint is **read-only** and **additive**. It is NOT I/O-free: it probes
SearXNG over the network on every call (``probe_health()``, 3 s cap), and the UI
polls it, so a hung instance costs one request per poll. The docstring used to
claim "fast (no I/O)", which is worth correcting rather than trusting.
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
    """Recon status for the runtime bar. No TOR fields: it was removed from the
    panel, the config and the capability list, and a payload that still carries
    it invites the next reader to wire it back up.

    No ``circuit_states`` either, for the same reason. It was declared here and
    on the frontend's ``IGINOCapabilities``, populated by nothing, and read by
    nobody -- a permanently empty dict that looked like a feature. The real
    per-thread circuit snapshot lives on ``/api/browser/health``
    (``browser_health.py``), which actually fills it."""

    enabled: bool = False
    searxng_healthy: bool = False
    cache_stats: dict[str, Any] = Field(default_factory=dict)
    audit_stats: dict[str, Any] = Field(default_factory=dict)


class FeatureFlags(BaseModel):
    """Server-declared feature switches the UI gates navigation on.

    Each maps to a config section's ``enabled`` (or, for ACP, to whether any
    agent is configured). New features add a field here and in
    ``frontend/src/features/types.ts``; a flag the frontend does not know is
    simply ignored.
    """

    jobs: bool = False
    integrations: bool = False
    email_marketing: bool = False
    acp_agents: bool = False
    capabilities: bool = False
    runtimes: bool = False


class CapabilitiesResponse(BaseModel):
    features: FeatureFlags = Field(default_factory=FeatureFlags)
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

    ``subagent_enabled=True`` matches the gateway's own runs. MCP tools are
    excluded: they are reported separately, and resolving them here would make a
    UI poll wait on remote servers.

    **The skill tool-policy is applied here, through the same function the agent
    uses.** It was not, and the omission was expensive. This docstring already
    claimed the report "cannot drift from what the model is really given" — but
    ``get_available_tools()`` returns the *registry*, and the lead agent then
    filters it through ``filter_tools_by_skill_allowed_tools``. When one public
    skill's ``allowed-tools`` collapsed the bound set from 42 tools to 6, this
    panel kept reporting 42 for five days, so the one instrument an operator
    checks to answer "can it still spawn subagents?" was actively confirming a
    capability that no longer existed.

    This reports the **default agent's** binding. A custom agent that names
    ``skills:`` in its config may legitimately bind fewer.
    """
    try:
        from deerflow.agents.lead_agent.agent import skills_for_tool_policy
        from deerflow.agents.manifest import _TOOL_PURPOSE_OVERRIDES
        from deerflow.skills.tool_policy import filter_tools_by_skill_allowed_tools
        from deerflow.tools.tools import get_available_tools

        tools = get_available_tools(
            include_mcp=False,
            subagent_enabled=True,
            app_config=config,
        )
        # ``None`` is the default agent's skill selection -- the same argument
        # ``_make_lead_agent`` passes for a run with no agent_name. Going through
        # the shared helper rather than reimplementing the rule is the point:
        # the two cannot disagree again.
        tools = filter_tools_by_skill_allowed_tools(tools, skills_for_tool_policy(None, app_config=config))

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
def _safe_hooks(config: AppConfig) -> list[HookSummary]:
    """List active middlewares by building the real lead-agent chain.

    Constructors are cheap (the `lazy_init=True` path); heavy resources
    initialize on the first agent run, not here. Pinned against the real chain
    by ``test_hooks_reflect_real_middleware_chain``.

    On failure this returns **nothing**, and that is the point. It used to fall
    back to an 11-name literal, which the bar rendered identically to eleven
    real hooks -- so a broken chain looked like a working one, and the operator
    reading the bar to find out what is loaded was told a confident lie. Eleven
    invented hooks are worse than a gap: the gap is legible. Logged at warning
    rather than debug for the same reason -- this is the only path that makes
    the count wrong, so it should not be silent.
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
        logger.warning("capabilities: middleware chain build failed; reporting no hooks: %s", e)
    return []


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


def _safe_server_info(config: AppConfig) -> dict[str, Any]:
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
        #
        # Report the CONFIGURED value. This used to report the source constant
        # unconditionally, while a run actually used config.subagents
        # .max_concurrent -- so the bar could confidently state a number the
        # runtime was not using.
        info["max_concurrent_subagents"] = getattr(
            getattr(config, "subagents", None),
            "max_concurrent",
            MAX_CONCURRENT_SUBAGENTS,
        )
    except Exception:  # pragma: no cover - constant import cannot realistically fail
        pass
    return info


async def _safe_igino() -> IGINOSummary:
    """Read-only Recon status snapshot."""
    try:
        enabled = os.environ.get("DEERFLOW_IGINO_ENABLED", "false").lower() in ("true", "1", "yes")

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
            searxng_healthy=searxng_healthy,
            cache_stats=cache_stats,
            audit_stats=audit_stats,
        )
    except Exception as e:
        logger.debug("capabilities: igino snapshot failed: %s", e)
        return IGINOSummary()


def _safe_features(config: AppConfig) -> FeatureFlags:
    """Flags from the one place that computes them (the ``features``
    capability module); a flag this response model does not declare is
    dropped, so the two lists are pinned together by test."""
    from deerflow.capabilities.modules.features import compute_flags

    try:
        flags = compute_flags(config)
    except Exception:  # config unreadable mid-reload: everything reads as off
        flags = {}
    return FeatureFlags(**{k: v for k, v in flags.items() if k in FeatureFlags.model_fields})


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
        features=_safe_features(config),
        skills=_safe_skills(config),
        tools=_safe_tools(config),
        hooks=_safe_hooks(config),
        subagents=_safe_subagents(config),
        circuits=_safe_circuits(config),
        igino=await _safe_igino(),
        server=_safe_server_info(config),
    )
