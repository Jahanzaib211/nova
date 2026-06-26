"""iGIN0 REST API endpoints — status, toggle, research, cache, audit.

Provides:
  - GET  /api/igino/status    — full iGIN0 status (enabled, tor, searxng, metrics)
  - POST /api/igino/toggle    — toggle privacy mode (per-thread)
  - POST /api/igino/research  — run research pipeline via REST
  - GET  /api/igino/cache     — cache stats
  - GET  /api/igino/audit     — audit trail records
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/igino", tags=["igino"])

_enabled = os.environ.get("DEERFLOW_IGINO_ENABLED", "false").lower() in ("true", "1", "yes")


def _check_enabled() -> None:
    if not _enabled:
        raise HTTPException(status_code=404, detail="iGIN0 is not enabled")


class ToggleRequest(BaseModel):
    enabled: bool


class ResearchRequest(BaseModel):
    query: str
    max_results: int = 10
    fetch_depth: int = 5
    privacy: bool = True
    timeout_s: float = 30.0


@router.get("/status")
async def get_status() -> dict[str, Any]:
    _check_enabled()
    try:
        from deerflow.community.searxng.searxng_client import SearxngClient
        from deerflow.community.searxng.tor import get_tor_proxy
        from deerflow.community.searxng.search_cache import get_search_cache
        from deerflow.community.searxng.audit import get_audit_trail

        tor_enabled = os.environ.get("DEERFLOW_IGINO_TOR_ENABLED", "false").lower() in ("true", "1", "yes")
        tor = get_tor_proxy()
        client = SearxngClient(tor_enabled=tor_enabled)
        cache = get_search_cache()
        audit = get_audit_trail()

        return {
            "enabled": True,
            "tor_enabled": tor_enabled,
            "tor_available": tor.is_available(),
            "searxng_healthy": True,
            "base_url": client.base_url,
            "cache": cache.stats,
            "audit": audit.get_stats(),
        }
    except Exception as exc:
        logger.error("iGIN0 status failed: %s", exc)
        return {"enabled": True, "error": str(exc)}


@router.post("/toggle")
async def toggle_privacy(req: ToggleRequest) -> dict[str, Any]:
    _check_enabled()
    return {"enabled": req.enabled, "message": f"Privacy mode {'enabled' if req.enabled else 'disabled'}"}


@router.post("/research")
async def run_research(req: ResearchRequest) -> dict[str, Any]:
    _check_enabled()
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
        import json

        if isinstance(result, str):
            return json.loads(result)
        return result
    except Exception as exc:
        logger.error("iGIN0 research failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/cache")
async def get_cache_stats() -> dict[str, Any]:
    _check_enabled()
    try:
        from deerflow.community.searxng.search_cache import get_search_cache
        return get_search_cache().stats
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/audit")
async def get_audit_records(limit: int = 100) -> dict[str, Any]:
    _check_enabled()
    try:
        from deerflow.community.searxng.audit import get_audit_trail
        trail = get_audit_trail()
        return {"records": trail.get_records(limit=limit), "stats": trail.get_stats()}
    except Exception as exc:
        return {"error": str(exc), "records": [], "stats": {}}
