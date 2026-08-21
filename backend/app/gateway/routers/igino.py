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
            provider = str(use).split(".")[-1].split(":")[0] or "unknown"
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
        }
    except Exception as exc:
        logger.error("iGIN0 status failed: %s", exc)
        return {"enabled": True, "error": "internal error"}


@router.post("/toggle")
async def toggle_privacy(req: ToggleRequest, user: Any | None = Depends(get_optional_user_from_request)) -> dict[str, Any]:
    _require_enabled_and_user(user)
    return {"enabled": req.enabled, "message": f"Privacy mode {'enabled' if req.enabled else 'disabled'}"}


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
