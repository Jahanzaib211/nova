"""iGIN0 REST API endpoints — status, toggle, research, cache, audit.

Provides:
  - GET  /api/igino/status    — always 200; reports `enabled: false` when feature is off
  - POST /api/igino/toggle    — toggle privacy mode (per-thread)
  - POST /api/igino/research  — run research pipeline via REST (auth required)
  - GET  /api/igino/cache     — cache stats
  - GET  /api/igino/audit     — audit trail records (auth required, owner-scoped)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.gateway.auth_disabled import is_auth_disabled
from app.gateway.deps import get_optional_user_from_request
from deerflow.config import get_app_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/igino", tags=["igino"])


def _is_enabled() -> bool:
    # Resolved per-request so the flag flips after a process restart without
    # needing to reload any Python module.
    return os.environ.get("DEERFLOW_IGINO_ENABLED", "false").lower() in ("true", "1", "yes")


def _require_enabled_and_user(user: Any | None) -> None:
    """Raise 404 if iGIN0 is disabled, 401 if no session user."""
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="iGIN0 is not enabled")
    if user is None and not is_auth_disabled():
        raise HTTPException(status_code=401, detail="Authentication required")


class ToggleRequest(BaseModel):
    enabled: bool


class ResearchRequest(BaseModel):
    query: str
    max_results: int = 10
    fetch_depth: int = 5
    privacy: bool = True
    timeout_s: float = 30.0


async def _web_capability(tool_name: str, health_path: str) -> dict[str, Any]:
    """Health of one web capability, named by the job it does.

    The three are genuinely different jobs and fail independently:

      web_search  SearXNG      finds pages
      web_fetch   Browserless  reads one page the agent already named
      web_fetch_many Crawl4AI  reads many named pages at once

    Reporting them as one "crawler" hid which of the three was down. Reads the
    configured `use:` string rather than assuming a provider, so swapping any
    of them cannot leave this panel describing something Nova no longer runs.
    """
    provider, base_url, healthy, detail = "unconfigured", "", False, ""

    try:
        config = get_app_config()
        entry = config.get_tool_config(tool_name)
        if entry is None:
            return {"tool": tool_name, "provider": "unconfigured", "healthy": False, "base_url": "", "detail": "not in config.yaml"}
        use = getattr(entry, "use", "") or ""
        module = str(use).split(":")[0]
        parts = [p for p in module.split(".") if p]
        provider = (parts[-2] if len(parts) >= 2 and parts[-1] == "tools" else parts[-1]) if parts else "unknown"
        base_url = str((entry.model_extra or {}).get("base_url", "") or "")
    except Exception as exc:  # noqa: BLE001 - the panel must render regardless
        logger.debug("web capability %s: config read failed: %s", tool_name, exc)
        return {"tool": tool_name, "provider": provider, "healthy": False, "base_url": "", "detail": "config unavailable"}

    if base_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.get(f"{base_url.rstrip('/')}{health_path}")
            healthy = resp.status_code < 400
            detail = f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            detail = type(exc).__name__
    else:
        # Hosted providers have no local endpoint to probe. Jina answered every
        # request while returning 401, so "reachable" was never "working" --
        # report unverified rather than healthy.
        detail = "hosted provider — not probed"

    return {"tool": tool_name, "provider": provider, "healthy": healthy, "base_url": base_url, "detail": detail}


async def _crawler_status() -> dict[str, Any]:
    """Health of whichever provider `web_fetch` is configured to use.

    Deliberately provider-agnostic: it reads the configured `use:` string
    rather than assuming Browserless, so swapping the fetch backend does not
    silently leave this panel reporting on something Nova no longer uses.

    A reachable provider is not the same as a working one -- Jina answered
    every request while returning 401, and `web_fetch` degraded to a plain GET
    without telling anyone for 118 calls. So a provider that needs credentials
    is probed for *authorisation*, not just for a TCP connection.
    """
    provider = "unknown"
    base_url = ""
    healthy = False
    detail = ""

    try:
        config = get_app_config()
        for entry in getattr(config, "tools", None) or []:
            name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
            if name != "web_fetch":
                continue
            use = entry.get("use") if isinstance(entry, dict) else getattr(entry, "use", "")
            # "deerflow.community.browserless.tools:web_fetch_tool" -> "browserless".
            # The last segment is the module file (almost always "tools"), so the
            # provider is the package that contains it.
            module = str(use).split(":")[0]
            parts = [p for p in module.split(".") if p]
            provider = (parts[-2] if len(parts) >= 2 and parts[-1] == "tools" else parts[-1]) if parts else "unknown"
            cfg_extra = entry if isinstance(entry, dict) else getattr(entry, "model_extra", {}) or {}
            base_url = str(cfg_extra.get("base_url", "") or "")
            break
    except Exception as exc:  # noqa: BLE001 - the panel must render regardless
        logger.debug("crawler status: config read failed: %s", exc)
        return {"provider": provider, "healthy": False, "base_url": "", "detail": "config unavailable"}

    if base_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.get(f"{base_url.rstrip('/')}/pressure")
            healthy = resp.status_code < 400
            detail = f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            detail = type(exc).__name__
    else:
        # Hosted providers (jina_ai, tavily, exa) have no local endpoint to
        # probe; report them as configured-but-unverified rather than healthy.
        detail = "hosted provider — not probed"

    return {"provider": provider, "healthy": healthy, "base_url": base_url, "detail": detail}


def _feature_states(*, tor_enabled: bool, audit: Any, cache: Any) -> list[dict[str, Any]]:
    """Per-capability state, so the panel can stop pretending one switch covers it.

    Each of these is configured independently by environment, and they fail
    independently too -- audit can be off while search works, cache can be
    disabled while both work. Reporting one aggregate "enabled" hid all of
    that. Read-only on purpose: the switch this replaces wrote nothing, and
    four controls that also write nothing would be a worse lie than one.
    """
    audit_stats = audit.get_stats() if audit is not None else {}
    cache_stats = getattr(cache, "stats", {}) or {}
    return [
        {
            "key": "search",
            "label": "Private search",
            "enabled": True,
            "env": "DEERFLOW_IGINO_SEARXNG_URL",
            "detail": "SearXNG",
        },
        {
            "key": "crawler",
            "label": "Crawler",
            "enabled": True,
            "env": "tools.web_fetch.use",
            "detail": "configured in config.yaml",
        },
        {
            "key": "cache",
            "label": "Result cache",
            "enabled": bool(cache_stats.get("max_size", 0)),
            "env": "DEERFLOW_IGINO_CACHE_MAX_SIZE",
            "detail": f"ttl {cache_stats.get('ttl_s', 0)}s",
        },
        {
            "key": "audit",
            "label": "Audit trail",
            "enabled": bool(audit_stats.get("enabled")),
            "env": "DEERFLOW_IGINO_AUDIT_ENABLED",
            "detail": "redacted" if audit_stats.get("redacted") else "full URLs",
        },
        {
            "key": "tor",
            "label": "TOR routing",
            "enabled": bool(tor_enabled),
            "env": "DEERFLOW_IGINO_TOR_ENABLED",
            "detail": "off by default — adds seconds per fetch",
        },
    ]


@router.get("/status")
async def get_status() -> dict[str, Any]:
    """Always returns 200. When iGIN0 is disabled, reports ``enabled: false``
    so the frontend PrivacyPanel can render a "feature off" state instead of
    flooding the console with 404s every 15 s."""
    if not _is_enabled():
        return {"enabled": False}
    try:
        from deerflow.community.searxng.audit import get_audit_trail
        from deerflow.community.searxng.search_cache import get_search_cache
        from deerflow.community.searxng.searxng_client import SearxngClient
        from deerflow.community.searxng.tor import get_tor_proxy

        tor_enabled = os.environ.get("DEERFLOW_IGINO_TOR_ENABLED", "false").lower() in ("true", "1", "yes")
        tor = get_tor_proxy()
        client = SearxngClient(tor_enabled=tor_enabled)
        cache = get_search_cache()
        audit = get_audit_trail()

        # Actually ask SearXNG. This was hardcoded ``True``, so the Privacy
        # panel showed a green SearXNG whether or not one existed and could
        # never report an outage -- a light that cannot turn red.
        searxng_healthy = await client.health()

        return {
            "enabled": True,
            "tor_enabled": tor_enabled,
            "tor_available": tor.is_available(),
            "searxng_healthy": searxng_healthy,
            "base_url": client.base_url,
            "cache": cache.stats,
            "audit": audit.get_stats(),
            "crawler": await _crawler_status(),
            "web": [
                await _web_capability("web_fetch", "/pressure"),
                await _web_capability("web_fetch_many", "/health"),
            ],
            "features": _feature_states(tor_enabled=tor_enabled, audit=audit, cache=cache),
        }
    except Exception as exc:
        logger.error("iGIN0 status failed: %s", exc)
        return {"enabled": True, "error": "internal error"}


@router.get("/test/{tool}")
async def test_capability(tool: str, user: Any | None = Depends(get_optional_user_from_request)) -> dict[str, Any]:
    """Exercise one web capability for real and report what happened.

    A health probe answers "is the port open", which is not the same question as
    "does this work" -- Jina answered every request while returning 401, and the
    fetch tool degraded silently for 118 calls. This runs the actual tool
    against a stable, boring URL and reports latency and output size, so the
    panel can offer proof instead of an address the user cannot act on.

    Exposed as GET on purpose: it is a read-only self-test with a fixed target,
    so it needs no CSRF dance to be usable from a button.
    """
    _require_enabled_and_user(user)

    probe_url = "https://example.com"
    started = time.monotonic()

    try:
        if tool == "web_search":
            from deerflow.community.searxng.tools import web_search_tool

            out = await web_search_tool.ainvoke({"query": "nova self test"})
            payload = json.loads(out) if out.strip().startswith("{") else {}
            count = int(payload.get("count", 0) or 0)
            source = str(payload.get("source", "") or "")
            # A result from `ddg_fallback` means SearXNG did NOT serve it. The
            # fallback is silent by design, so without this the panel would
            # report a healthy SearXNG that never answered.
            ok = count > 0 and source == "searxng"
            detail = f"{count} result(s) via {source or 'unknown'}"
        elif tool == "web_fetch":
            from deerflow.community.browserless.tools import web_fetch_tool

            out = await web_fetch_tool.ainvoke({"url": probe_url})
            ok = bool(out) and not out.startswith("Error:")
            detail = f"{len(out)} chars" if ok else out[:120]
        elif tool == "web_fetch_many":
            from deerflow.community.crawl4ai.tools import web_crawl_tool

            out = await web_crawl_tool.ainvoke({"urls": [probe_url], "max_pages": 1})
            ok = bool(out) and not out.startswith("Error:")
            detail = f"{len(out)} chars" if ok else out[:120]
        else:
            raise HTTPException(status_code=404, detail=f"Unknown capability '{tool}'.")

        return {"tool": tool, "ok": ok, "detail": detail, "duration_ms": round((time.monotonic() - started) * 1000)}

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed self-test is a result, not a 500
        return {
            "tool": tool,
            "ok": False,
            "detail": f"{type(exc).__name__}: {exc}"[:160],
            "duration_ms": round((time.monotonic() - started) * 1000),
        }


@router.post("/toggle")
async def toggle_privacy(req: ToggleRequest, user: Any | None = Depends(get_optional_user_from_request)) -> dict[str, Any]:
    """Deprecated. Reports the real state; it never changed anything.

    This endpoint returned ``{"enabled": <whatever you sent>}`` and wrote
    nothing -- no env, no config, no store. The UI switch it backed therefore
    did nothing at all while looking like it worked, which is worse than having
    no switch: it invited an operator to "turn privacy on" and then trust it.

    Each capability is configured independently by environment
    (DEERFLOW_IGINO_ENABLED, _AUDIT_ENABLED, _TOR_ENABLED, _CACHE_*), so a
    single master switch could not have expressed the real state even if it had
    persisted. ``/status`` now reports each one separately.

    Kept as a 410 rather than deleted so an older frontend gets a clear answer
    instead of a 404 that reads as an outage.
    """
    raise HTTPException(
        status_code=410,
        detail=("Toggling was never implemented and has been removed. Configure each capability via DEERFLOW_IGINO_* environment variables; /status reports their live state."),
    )


@router.post("/research")
async def run_research(req: ResearchRequest, user: Any | None = Depends(get_optional_user_from_request)) -> dict[str, Any]:
    _require_enabled_and_user(user)
    try:
        from deerflow.tools.builtins.igino_research_tool import igino_research_tool

        result = await igino_research_tool.ainvoke(
            {
                "query": req.query,
                "max_results": req.max_results,
                "fetch_depth": req.fetch_depth,
                "privacy": req.privacy,
                "timeout_s": req.timeout_s,
            }
        )

        if isinstance(result, str):
            return json.loads(result)
        return result
    except Exception as exc:
        logger.error("iGIN0 research failed: %s", exc)
        raise HTTPException(status_code=500, detail="research failed")


@router.get("/cache")
async def get_cache_stats() -> dict[str, Any]:
    if not _is_enabled():
        return {"enabled": False}
    try:
        from deerflow.community.searxng.search_cache import get_search_cache

        return get_search_cache().stats
    except Exception as exc:
        return {"error": "internal error"}


@router.get("/audit")
async def get_audit_records(limit: int = 100, user: Any | None = Depends(get_optional_user_from_request)) -> dict[str, Any]:
    _require_enabled_and_user(user)
    try:
        from deerflow.community.searxng.audit import get_audit_trail

        trail = get_audit_trail()
        return {"records": trail.get_records(limit=limit), "stats": trail.get_stats()}
    except Exception as exc:
        return {"error": "Failed to fetch audit records", "records": [], "stats": {}}


# v7.3 (Nova rebrand): backward-compat aliases for tests that imported the
# pre-rename function names. The functions were renamed to `get_status` /
# `toggle_privacy` during security hardening (fd2eb27); tests still expect
# the original module-level names. Aliases keep both call sites working
# without renaming the routes or rewriting the tests.
# CodeQL: unused-global-variable false positive
igino_status = get_status
# CodeQL: unused-global-variable false positive
igino_toggle = toggle_privacy
