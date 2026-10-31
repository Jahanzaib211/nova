"""iGIN0 REST API endpoints — status, self-test, research, cache, audit.

Provides:
  - GET  /api/igino/status    — always 200; reports `enabled: false` when feature is off
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


class ResearchRequest(BaseModel):
    query: str
    max_results: int = 10
    fetch_depth: int = 5
    privacy: bool = True
    timeout_s: float = 30.0


def _provider_from_use(use: str) -> str:
    """The package behind a ``use:`` string.

    ``deerflow.community.searxng.tools:web_search_tool`` -> ``searxng``. The
    last module segment is almost always ``tools``, so the provider is the
    package containing it.
    """
    parts = [p for p in str(use).split(":")[0].split(".") if p]
    if not parts:
        return "unknown"
    return parts[-2] if len(parts) >= 2 and parts[-1] == "tools" else parts[-1]


def _tool_endpoint(tool_name: str) -> tuple[str, str] | None:
    """``(provider, base_url)`` for a configured tool, ``None`` when it is not configured.

    Read from config rather than hardcoded: the provider for these tools
    changed twice in one day, and a panel that names the wrong one is worse
    than a panel that names none. Raises when the config cannot be read.
    """
    entry = get_app_config().get_tool_config(tool_name)
    if entry is None:
        return None
    return _provider_from_use(getattr(entry, "use", "") or ""), str((entry.model_extra or {}).get("base_url", "") or "")


def _provider_for(tool_name: str) -> str:
    try:
        endpoint = _tool_endpoint(tool_name)
    except Exception as exc:  # noqa: BLE001 - the panel must render regardless
        logger.debug("provider lookup for %s failed: %s", tool_name, exc)
        return "unknown"
    return endpoint[0] if endpoint is not None else "unconfigured"


#: One ``/status`` call's probe results, keyed by URL. ``web_fetch_many`` and
#: ``web_crawl`` both sit on the Crawl4AI service, and the old ``fetch`` block
#: re-probed Browserless after ``web_fetch`` already had — so one status poll
#: hit each local service twice.
ProbeMemo = dict[str, tuple[bool, str]]


async def _probe_endpoint(url: str, memo: ProbeMemo) -> tuple[bool, str]:
    if url not in memo:
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.get(url)
            healthy = resp.status_code < 400
            memo[url] = (healthy, "reachable" if healthy else f"refused (HTTP {resp.status_code})")
        except Exception as exc:  # noqa: BLE001
            memo[url] = (False, type(exc).__name__)
    return memo[url]


async def _web_capability(tool_name: str, health_path: str, memo: ProbeMemo | None = None) -> dict[str, Any]:
    """Health of one web capability, named by the job it does.

    The jobs are genuinely different and fail independently:

      web_search  SearXNG      finds pages
      web_fetch   Browserless  reads one page the agent already named
      web_fetch_many Crawl4AI  reads many named pages at once

    Reporting them as one "crawler" hid which was down. Reads the configured
    ``use:`` string rather than assuming a provider, so swapping any of them
    cannot leave this panel describing something Nova no longer runs.

    A reachable provider is not the same as a working one -- Jina answered
    every request while returning 401 -- so a hosted provider with no local
    endpoint is reported unverified, never healthy.
    """
    try:
        endpoint = _tool_endpoint(tool_name)
    except Exception as exc:  # noqa: BLE001 - the panel must render regardless
        logger.debug("web capability %s: config read failed: %s", tool_name, exc)
        return {"tool": tool_name, "provider": "unconfigured", "healthy": False, "detail": "config unavailable"}
    if endpoint is None:
        return {"tool": tool_name, "provider": "unconfigured", "healthy": False, "detail": "not in config.yaml"}
    provider, base_url = endpoint
    if not base_url:
        return {"tool": tool_name, "provider": provider, "healthy": False, "detail": "hosted provider — not probed"}
    healthy, detail = await _probe_endpoint(f"{base_url.rstrip('/')}{health_path}", memo if memo is not None else {})
    return {"tool": tool_name, "provider": provider, "healthy": healthy, "detail": detail}


def _feature_states(*, audit: Any, cache: Any) -> list[dict[str, Any]]:
    """The cross-cutting features, and only those.

    This used to also list "Private search" and "Crawler". Both were dropped:
    the Pipeline section reports search and fetch with live health and a
    self-test button, so repeating them here said the same thing twice and
    said it worse -- a static `enabled: True` cannot go red. "Crawler" was
    also the wrong word for what web_fetch does; Browserless renders one URL
    and follows nothing.

    TOR is gone entirely rather than reported as off. It costs seconds per
    fetch, nothing here routes through it, and a permanently-off row invites
    someone to turn on a latency tax for a privacy property this deployment
    does not claim.

    What remains is configured independently by environment and fails
    independently -- audit can be off while search works, cache can be
    disabled while both work. Read-only on purpose: the switch this replaces
    wrote nothing, and controls that also write nothing would be a worse lie.
    """
    audit_stats = audit.get_stats() if audit is not None else {}
    cache_stats = getattr(cache, "stats", {}) or {}
    return [
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

        client = SearxngClient()
        cache = get_search_cache()
        audit = get_audit_trail()

        # Actually ask SearXNG. This was hardcoded ``True``, so the Privacy
        # panel showed a green SearXNG whether or not one existed and could
        # never report an outage -- a light that cannot turn red.
        searxng_healthy = await client.health()

        memo: ProbeMemo = {}
        web_fetch = await _web_capability("web_fetch", "/pressure", memo)

        return {
            "enabled": True,
            "searxng_healthy": searxng_healthy,
            "cache": cache.stats,
            "audit": audit.get_stats(),
            # The web_fetch provider's health, named for the tool -- the same
            # probe as the web_fetch row below, not a second one.
            "fetch": {k: web_fetch[k] for k in ("provider", "healthy", "detail")},
            # The pipeline, in the order a request actually flows through it.
            # web_search is a member like any other: the frontend used to carry
            # its own hardcoded four-step list and special-case search out of
            # this array, so adding or renaming a capability meant editing the
            # panel. The order here is the order rendered.
            "web": [
                # Health comes from the same probe as `searxng_healthy` above,
                # so the card and the pipeline row can never disagree.
                {
                    "tool": "web_search",
                    "provider": _provider_for("web_search"),
                    "healthy": searxng_healthy,
                    "detail": "reachable" if searxng_healthy else "unreachable",
                },
                web_fetch,
                await _web_capability("web_fetch_many", "/health", memo),
                # web_crawl runs Nova's own BFS over the crawl4ai service, so
                # its health is that service's health -- but it is reported
                # separately because the two fail for different reasons: the
                # service being down, versus a crawl that robots.txt refused.
                # Same service and path as web_fetch_many: probed once.
                await _web_capability("web_crawl", "/health", memo),
            ],
            "features": _feature_states(audit=audit, cache=cache),
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
            from deerflow.community.crawl4ai.tools import web_fetch_many_tool

            out = await web_fetch_many_tool.ainvoke({"urls": [probe_url], "max_pages": 1})
            ok = bool(out) and not out.startswith("Error:")
            detail = f"{len(out)} chars" if ok else out[:120]
        elif tool == "web_crawl":
            from deerflow.community.crawl4ai.crawl_tool import crawl_site

            # crawl_site rather than the tool wrapper: the wrapper returns
            # markdown, and "1200 chars" is exactly the kind of number that
            # looks like success whether or not a single link was followed.
            # Pages and links are the two figures that can only be non-trivial
            # if the crawl actually crawled.
            result = await crawl_site(probe_url, max_pages=3, max_depth=1)
            pages = len(result["pages"])
            links = sum(len(v) for v in result["graph"].values())
            ok = pages > 0 and not result["stopped"].startswith("error:")
            detail = f"{pages} page(s), {links} link(s), stopped: {result['stopped']}"
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


# v7.3 (Nova rebrand): backward-compat alias for tests that imported the
# pre-rename function name (renamed to `get_status` in fd2eb27).
# CodeQL: unused-global-variable false positive
igino_status = get_status
