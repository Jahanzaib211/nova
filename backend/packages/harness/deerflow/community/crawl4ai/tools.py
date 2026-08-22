"""`web_fetch_many` — read several pages you already named, in parallel.

This is the third and last of Nova's web capabilities, and they are genuinely
different jobs, which is why they are separate tools rather than one:

  web_search      SearXNG      "what pages exist about X?"  -> a list of URLs
  web_fetch       Browserless  "read this one page"         -> one article
  web_fetch_many  Crawl4AI     "read all of these pages"    -> many articles
  web_crawl       (this + BFS) "start here and follow links" -> a site subtree

This one is batch retrieval, NOT link-following, and the name says so. Crawl4AI
can follow links as a library, but its REST API validates every config under an
untrusted trust boundary and rejects `deep_crawl_strategy` outright -- an
HTTP-reachable link-following crawler is an SSRF amplifier, so upstream forbids
it with no opt-in. Calling this tool "crawl" would have promised the agent a
capability the server refuses to perform.

Link-following lives in `crawl_tool.py` instead, where Nova owns the loop and
therefore owns the limits: the BFS, the caps and the robots.txt check are all
ours, and it drives this same batch client one depth at a time.

What it does buy: search returns ten URLs, and reading them was ten sequential
web_fetch calls. This is one call, fetched in parallel, returned as markdown.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain.tools import tool

from deerflow.config import get_app_config

from .crawl4ai_client import DEFAULT_BASE_URL, Crawl4aiClient

logger = logging.getLogger(__name__)

# A crawl can grow without bound; these bound it before the agent's numbers do.
_MAX_PAGES_CEILING = 25
_PER_PAGE_CHARS = 4096
_TOTAL_CHARS = 20000


def _get_tool_config(tool_name: str) -> dict | None:
    config = get_app_config().get_tool_config(tool_name)
    if config is None:
        return None
    extras = config.model_extra
    return extras if extras is not None else {}


def _get_client() -> Crawl4aiClient:
    cfg = _get_tool_config("web_fetch_many") or {}
    return Crawl4aiClient(
        base_url=cfg.get("base_url", DEFAULT_BASE_URL),
        token=cfg.get("token", ""),
        timeout_s=float(cfg.get("timeout_s", 90.0)),
    )


def _record(url: str, *, pages: int, success: bool, duration_ms: float, error: str | None = None) -> None:
    """Record the crawl in the shared audit trail. Never fatal."""
    try:
        from deerflow.community.searxng.audit import get_audit_trail

        get_audit_trail().fetch(
            url=url,
            source="crawl4ai",
            success=success,
            duration_ms=duration_ms,
            error=error,
        )
    except Exception:  # noqa: BLE001 - bookkeeping must not break the tool
        logger.debug("crawl audit failed for %s", url, exc_info=True)


@tool("web_fetch_many", parse_docstring=True)
async def web_fetch_many_tool(urls: list[str], max_pages: int = 10) -> str:
    """Fetch several web pages in one call and return them as markdown.

    Use this after web_search, when you want to read most of the results: it
    fetches them in parallel instead of one web_fetch call per URL. For a single
    page, use web_fetch. This does not follow links -- pass the URLs you want.

    Args:
        urls: The page URLs to fetch. Each must include the scheme (https://example.com). Capped at 25.
        max_pages: Maximum number of pages to return. Capped at 25.
    """
    wanted = [u for u in (urls or []) if isinstance(u, str) and u.strip()][:_MAX_PAGES_CEILING]
    if not wanted:
        return "Error: no URLs supplied."
    pages = max(1, min(int(max_pages), _MAX_PAGES_CEILING, len(wanted)))
    start = time.monotonic()

    try:
        results = await _get_client().crawl(wanted, max_pages=pages)
        elapsed_ms = (time.monotonic() - start) * 1000

        if not results:
            _record(wanted[0], pages=0, success=False, duration_ms=elapsed_ms, error="no results")
            return f"Error: fetching {len(wanted)} page(s) returned nothing."

        chunks: list[str] = []
        total = 0
        for item in results:
            page_url = item.get("url", "")
            md = item.get("markdown") or item.get("cleaned_html") or ""
            if isinstance(md, dict):  # newer versions nest the markdown variants
                md = md.get("raw_markdown") or md.get("fit_markdown") or ""
            md = str(md)[:_PER_PAGE_CHARS]
            if not md.strip():
                continue
            block = f"## {page_url}\n\n{md}"
            if total + len(block) > _TOTAL_CHARS:
                chunks.append(f"\n_(truncated — {len(results) - len(chunks)} more page(s) not shown)_")
                break
            chunks.append(block)
            total += len(block)

        # One audit record per page, so the panel's fetch counters reflect pages
        # retrieved rather than tool calls made.
        for item in results[: len(chunks)]:
            _record(item.get("url", ""), pages=1, success=True, duration_ms=elapsed_ms / max(len(results), 1))

        return f"Fetched {len(chunks)} page(s):\n\n" + "\n\n---\n\n".join(chunks)

    except Exception as exc:  # noqa: BLE001 - surface as tool output, not a crash
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.error("web_fetch_many failed: %s", exc)
        _record(wanted[0], pages=0, success=False, duration_ms=elapsed_ms, error=str(exc)[:200])
        return f"Error: {exc}"


__all__ = ["web_fetch_many_tool"]
