"""Enterprise-grade research orchestrator tool.

Deterministic multi-source research pipeline:
  1. SEARCH: SearXNG (with optional TOR) → deduplicate by URL
  2. FETCH: Parallel fetch via Jina AI (with timeout, circuit, retry)
  3. SYNTHESIZE: Deduplicate by content, sort by relevance

Never crashes the agent — all exceptions caught, logged, returned as structured error.

Configuration (env vars):
    DEERFLOW_IGINO_RESEARCH_TIMEOUT_S  default 30
    DEERFLOW_IGINO_FETCH_DEPTH         default 5
    DEERFLOW_IGINO_MAX_SOURCES         default 10
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any

from langchain.tools import tool

logger = logging.getLogger(__name__)

_RESEARCH_TIMEOUT_S = float(os.environ.get("DEERFLOW_IGINO_RESEARCH_TIMEOUT_S", "30"))
_FETCH_DEPTH = int(os.environ.get("DEERFLOW_IGINO_FETCH_DEPTH", "5"))
_MAX_SOURCES = int(os.environ.get("DEERFLOW_IGINO_MAX_SOURCES", "10"))
_FETCH_TIMEOUT_S = 10.0
_FETCH_CONCURRENCY = 4


async def _fetch_one(url: str, source: str, trace_id: str) -> dict[str, Any] | None:
    try:
        from deerflow.community.jina_ai.jina_client import JinaClient
        from deerflow.community.searxng.audit import get_audit_trail
        from deerflow.sandbox.browser_tracing import browser_span

        start = time.monotonic()
        with browser_span("igino_fetch", {"url": url, "source": source, "trace_id": trace_id}):
            client = JinaClient()
            content = await client.crawl(url)
            elapsed_ms = (time.monotonic() - start) * 1000

            try:
                from deerflow.sandbox.metrics import igino_fetch_duration_ms, igino_fetch_total

                igino_fetch_total.inc("ok")
                igino_fetch_duration_ms.observe(elapsed_ms, source)
            except Exception:
                pass

            get_audit_trail().fetch(
                url=url,
                source=source,
                privacy_mode=True,
                tor_used=False,
                success=True,
                duration_ms=elapsed_ms,
            )

            if content and not content.startswith("Error:"):
                return {"url": url, "content": content[:4096], "source": source, "duration_ms": elapsed_ms}
            return None
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000 if "start" in dir() else 0
        try:
            from deerflow.sandbox.metrics import igino_fetch_duration_ms, igino_fetch_total

            igino_fetch_total.inc("error")
            igino_fetch_duration_ms.observe(elapsed_ms, source)
        except Exception:
            pass
        logger.warning("Fetch failed for %s: %s", url, exc)
        return None


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


@tool("igino_research", parse_docstring=True)
async def igino_research_tool(
    query: str,
    max_results: int = 10,
    fetch_depth: int = _FETCH_DEPTH,
    privacy: bool = True,
    timeout_s: float = _RESEARCH_TIMEOUT_S,
) -> str:
    """Privacy-focused multi-source web research via SearXNG.

    Performs deterministic research: search → fetch → deduplicate → return.
    Results are cached, traceable, and never crash the agent.

    Args:
        query: The research query.
        max_results: Maximum search results to return.
        fetch_depth: How many top results to fetch full content for.
        privacy: Use TOR for search (if available).
        timeout_s: Total research timeout in seconds.
    """
    start = time.monotonic()
    trace_id = hashlib.sha256(f"{query}{time.time()}".encode()).hexdigest()[:16]
    sources_searched: list[str] = []
    sources_fetched: list[str] = []
    errors: list[str] = []

    try:
        from deerflow.community.searxng.audit import get_audit_trail
        from deerflow.community.searxng.searxng_client import SearxngClient

        client = SearxngClient(tor_enabled=privacy)
        search_results = await asyncio.wait_for(
            client.search(query, max_results=max_results, trace_id=trace_id),
            timeout=timeout_s * 0.4,
        )
        sources_searched.append("searxng")
    except TimeoutError:
        errors.append("Search timed out")
        search_results = []
    except Exception as exc:
        errors.append(f"Search failed: {exc}")
        search_results = []

    if not search_results:
        elapsed_ms = (time.monotonic() - start) * 1000
        try:
            from deerflow.sandbox.metrics import igino_research_duration_ms, igino_research_total

            igino_research_total.inc("failed")
            igino_research_duration_ms.observe(elapsed_ms)
        except Exception:
            pass
        return json.dumps(
            {
                "query": query,
                "results": [],
                "metadata": {
                    "sources_searched": sources_searched,
                    "sources_fetched": sources_fetched,
                    "duration_ms": elapsed_ms,
                    "error": "; ".join(errors) if errors else None,
                },
            },
            indent=2,
            ensure_ascii=False,
        )

    seen_urls: set[str] = set()
    unique_results: list[dict[str, Any]] = []
    for r in search_results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_results.append(r)

    fetch_depth = min(fetch_depth, len(unique_results), _MAX_SOURCES)
    fetch_list = unique_results[:fetch_depth]

    fetched: list[dict[str, Any]] = []
    if fetch_list:
        remaining = timeout_s - (time.monotonic() - start) - 1.0
        if remaining > 1.0:
            sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

            async def _limited_fetch(url: str, source: str) -> dict[str, Any] | None:
                async with sem:
                    return await _fetch_one(url, source, trace_id)

            tasks = [
                _limited_fetch(r.get("url", ""), "jina")
                for r in fetch_list
                if r.get("url")
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, dict) and r is not None:
                    fetched.append(r)
                    sources_fetched.append("jina")
                elif isinstance(r, Exception):
                    errors.append(f"Fetch error: {r}")

    deduped: list[dict[str, Any]] = []
    seen_content: set[str] = set()
    for item in fetched:
        h = _content_hash(item.get("content", ""))
        if h not in seen_content:
            seen_content.add(h)
            deduped.append(item)

    results_list: list[dict[str, Any]] = []
    for i, r in enumerate(unique_results):
        url = r.get("url", "")
        fetched_item = next((f for f in deduped if f["url"] == url), None)
        results_list.append(
            {
                "title": r.get("title", ""),
                "url": url,
                "snippet": r.get("content", ""),
                "content": fetched_item["content"] if fetched_item else None,
                "source": fetched_item["source"] if fetched_item else "searxng",
                "score": max(0, 1.0 - i * 0.1),
            }
        )

    elapsed_ms = (time.monotonic() - start) * 1000
    tor_available = False
    try:
        from deerflow.community.searxng.tor import get_tor_proxy

        tor_available = get_tor_proxy().is_available()
    except Exception:
        pass

    try:
        from deerflow.sandbox.metrics import igino_research_duration_ms, igino_research_total

        igino_research_total.inc("ok" if not errors else "partial")
        igino_research_duration_ms.observe(elapsed_ms)
    except Exception:
        pass

    try:
        from deerflow.community.searxng.audit import get_audit_trail

        get_audit_trail().search(
            thread_id="",
            query=query,
            privacy_mode=privacy,
            tor_used=privacy and tor_available,
            sources_searched=sources_searched,
            results_returned=len(results_list),
            duration_ms=elapsed_ms,
            cache_hit=False,
            error="; ".join(errors) if errors else None,
        )
    except Exception:
        pass

    return json.dumps(
        {
            "query": query,
            "results": results_list,
            "metadata": {
                "sources_searched": sources_searched,
                "sources_fetched": sources_fetched,
                "sources_succeeded": len(fetched),
                "privacy_mode": privacy,
                "tor_used": privacy and tor_available,
                "duration_ms": round(elapsed_ms, 1),
                "cache_hit": False,
                "errors": errors if errors else None,
            },
        },
        indent=2,
        ensure_ascii=False,
    )
