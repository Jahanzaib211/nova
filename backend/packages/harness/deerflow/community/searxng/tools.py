"""Enterprise-grade SearXNG search tool with TOR, fallback, and structured output.

Replaces the basic tool with a hardened version that:
  - Routes through TOR (optional, configurable per-call)
  - Falls back to DuckDuckGo if SearXNG is unavailable
  - Returns structured JSON with metadata
  - Records audit trail for compliance
  - Never crashes the agent — returns empty results on failure

Configuration (in config.yaml):
  - name: web_search
    group: web
    use: deerflow.community.searxng.tools:web_search_tool
    base_url: http://searxng:8080
    max_results: 5
    tor_enabled: false
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain.tools import tool

from deerflow.config import get_app_config

logger = logging.getLogger(__name__)


def _get_tool_config(tool_name: str) -> dict[str, Any] | None:
    config = get_app_config().get_tool_config(tool_name)
    if config is None:
        return None
    extras = config.model_extra
    return extras if extras is not None else {}


def _get_searxng_client(base_url: str, use_tor: bool) -> Any:
    """Construct a SearxngClient.

    Exposed at module scope so tests can patch it (see tests/test_searxng_client.py
    TestSearxngTools). Production callers do not override this.
    """
    from deerflow.community.searxng.searxng_client import SearxngClient

    return SearxngClient(base_url=base_url, tor_enabled=use_tor)


@tool("web_search", parse_docstring=True)
async def web_search_tool(query: str, tor: bool = False) -> str:
    """Search the web using SearXNG meta-search engine (privacy-first).

    Args:
        query: The search query.
        tor: Route through TOR for anonymity (optional).
    """
    start = time.monotonic()
    cfg = _get_tool_config("web_search") or {}
    base_url = cfg.get("base_url", "http://searxng:8080")
    max_results = int(cfg.get("max_results", 5))
    tor_default = cfg.get("tor_enabled", False)
    use_tor = tor or tor_default

    try:
        from deerflow.community.searxng.audit import get_audit_trail

        client = _get_searxng_client(base_url, use_tor)
        results = await client.search(query, max_results=max_results)
        elapsed_ms = (time.monotonic() - start) * 1000

        normalized = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in results
        ]

        get_audit_trail().search(
            query=query,
            privacy_mode=True,
            tor_used=use_tor,
            sources_searched=["searxng"],
            results_returned=len(normalized),
            duration_ms=elapsed_ms,
        )

        return json.dumps(
            {"query": query, "results": normalized, "count": len(normalized), "source": "searxng"},
            indent=2,
            ensure_ascii=False,
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.warning("SearXNG search failed, attempting DDG fallback: %s", exc)
        try:
            return await _ddg_fallback(query, max_results, start)
        except Exception as fallback_exc:
            logger.error("DDG fallback also failed: %s", fallback_exc)
            from deerflow.community.searxng.audit import get_audit_trail

            get_audit_trail().search(
                query=query,
                privacy_mode=False,
                tor_used=False,
                sources_searched=[],
                results_returned=0,
                duration_ms=elapsed_ms,
                error=str(exc),
            )
            return json.dumps(
                {"error": str(exc), "query": query, "results": [], "source": "none"},
                ensure_ascii=False,
            )


async def _ddg_fallback(query: str, max_results: int, start: float) -> str:
    from deerflow.community.ddg_search.tools import web_search_tool as ddg_tool

    raw = await ddg_tool.ainvoke({"query": query})
    elapsed_ms = (time.monotonic() - start) * 1000

    from deerflow.community.searxng.audit import get_audit_trail

    get_audit_trail().search(
        query=query,
        privacy_mode=False,
        tor_used=False,
        sources_searched=["ddg"],
        results_returned=max_results,
        duration_ms=elapsed_ms,
        cache_hit=False,
    )

    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, list):
            return json.dumps(
                {"query": query, "results": parsed, "count": len(parsed), "source": "ddg_fallback"},
                indent=2,
                ensure_ascii=False,
            )
    except (json.JSONDecodeError, TypeError):
        pass
    return raw if isinstance(raw, str) else json.dumps(raw)
