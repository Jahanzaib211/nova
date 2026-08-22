"""`web_crawl` — the first tool in Nova that actually follows links.

Everything else in the web stack reads pages someone already named. Search
returns URLs, `web_fetch` reads one, `web_fetch_many` reads a list. None of them
discovers a page you did not know about, which meant the "Crawler" the UI has
been showing since the beginning described nothing that existed.

Crawl4AI can follow links as a library, but not through the REST API we run:
it validates every config under an untrusted trust boundary and rejects
``deep_crawl_strategy`` outright, with no server-side opt-in. That is a
deliberate upstream decision -- a link-following crawler reachable over HTTP is
an open crawler, and an open crawler is an SSRF amplifier.

So the breadth-first search lives here instead, and that turns out to be the
better place for it. The expensive half -- fetching many URLs in parallel -- is
already built and already running as a memory-capped, health-checked service;
this module calls it once per depth. The cheap half is link extraction. What we
gain by owning the loop is that we own the limits: `max_pages`, `max_depth`,
`same_domain`, the wall-clock ceiling and the robots.txt check are all in code
here, where they can be read and tested, rather than being a config field on a
service that has already refused to honour it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from langchain.tools import tool

from deerflow.config import get_app_config

from .crawl4ai_client import DEFAULT_BASE_URL, Crawl4aiClient

logger = logging.getLogger(__name__)

# Ceilings the agent's own arguments are clamped to. A crawl is the one web
# tool whose cost is not bounded by its inputs -- one URL can reach the whole
# internet -- so the bound has to come from here.
_MAX_PAGES_CEILING = 50
_MAX_DEPTH_CEILING = 3
# Total wall clock for the whole crawl. One unresponsive host must not be able
# to hold a tool call open until the agent's own timeout fires.
_DEADLINE_S = 120.0
_PER_PAGE_CHARS = 3000
_TOTAL_CHARS = 24000
# Fetched once per host and reused for the crawl. Not a long-lived cache:
# a crawl is seconds long, and a stale robots.txt is exactly the thing that
# makes a crawler misbehave.
_ROBOTS_TIMEOUT_S = 5.0

# Extensions that are never worth a page fetch -- following them wastes the
# page budget on bytes no agent is going to read as text.
_SKIP_SUFFIXES = (
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".7z",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".webm",
    ".wav",
    ".css",
    ".js",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".exe",
    ".dmg",
    ".deb",
    ".rpm",
    ".apk",
)


class _LinkExtractor(HTMLParser):
    """Collect href targets. Stdlib rather than bs4/lxml on purpose.

    The gateway image should not grow a parser dependency to read `href`
    attributes, and HTMLParser is lenient about the broken markup that a crawl
    reaching arbitrary pages will certainly hit.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)

    def error(self, message: str) -> None:  # pragma: no cover - stdlib hook
        """HTMLParser's abstract error hook. Malformed markup is expected here
        and must not raise: a crawl that dies on one bad page is not a crawl."""


def _get_tool_config(tool_name: str) -> dict | None:
    config = get_app_config().get_tool_config(tool_name)
    if config is None:
        return None
    extras = config.model_extra
    return extras if extras is not None else {}


def _get_client() -> Crawl4aiClient:
    cfg = _get_tool_config("web_crawl") or {}
    return Crawl4aiClient(
        base_url=cfg.get("base_url", DEFAULT_BASE_URL),
        token=cfg.get("token", ""),
        timeout_s=float(cfg.get("timeout_s", 60.0)),
    )


def _normalise(base: str, href: str) -> str | None:
    """Resolve one href against its page, or reject it.

    Fragments are stripped rather than kept: `/docs#install` and `/docs` are the
    same fetch, and keeping both would spend two of the page budget on one page.
    """
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
        return None
    try:
        absolute = urljoin(base, href)
        absolute, _ = urldefrag(absolute)
        parsed = urlparse(absolute)
    except Exception:  # noqa: BLE001 - a malformed href is a skipped link
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.path.lower().endswith(_SKIP_SUFFIXES):
        return None
    return absolute


def _same_site(a: str, b: str) -> bool:
    """Host equality, ignoring a leading `www.`.

    `example.com` and `www.example.com` are one site to every human and two
    hosts to `urlparse`; treating them as different makes `same_domain=True`
    stop a crawl at its own front page.
    """
    ha = urlparse(a).netloc.lower().removeprefix("www.")
    hb = urlparse(b).netloc.lower().removeprefix("www.")
    return ha == hb


def _page_html(item: dict[str, Any]) -> str:
    for key in ("html", "cleaned_html", "raw_html"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _page_text(item: dict[str, Any]) -> str:
    md = item.get("markdown") or item.get("cleaned_html") or ""
    if isinstance(md, dict):  # newer versions nest the markdown variants
        md = md.get("raw_markdown") or md.get("fit_markdown") or ""
    return str(md)


def _links_from(item: dict[str, Any], page_url: str) -> list[str]:
    """Prefer Crawl4AI's own extracted links, fall back to parsing the HTML.

    The service returns a `links` object on most responses; when it does not --
    older builds, or a page it only partially rendered -- parsing the HTML we
    already have is free, and silently returning no links would look exactly
    like a page with no links.
    """
    out: list[str] = []
    raw = item.get("links")
    if isinstance(raw, dict):
        for group in ("internal", "external"):
            for entry in raw.get(group) or []:
                href = entry.get("href") if isinstance(entry, dict) else entry
                if isinstance(href, str):
                    out.append(href)
    if not out:
        parser = _LinkExtractor()
        try:
            parser.feed(_page_html(item))
        except Exception:  # noqa: BLE001 - one unparseable page is not a failure
            logger.debug("link extraction failed for %s", page_url, exc_info=True)
        out = parser.hrefs

    seen: set[str] = set()
    resolved: list[str] = []
    for href in out:
        url = _normalise(page_url, href)
        if url and url not in seen:
            seen.add(url)
            resolved.append(url)
    return resolved


class _RobotsGate:
    """robots.txt, honoured, with one fetch per host per crawl.

    This is the one limit here that is not about protecting Nova. Ignoring
    robots is how a crawler gets the deployment's IP blocked, and the public
    deployment shares that IP with everything else Nova does.

    `RobotFileParser.read()` is deliberately not used: it calls `urlopen`, which
    is blocking, and this runs on the gateway's event loop. Fetching with httpx
    and handing the lines to `parse()` gets the same parser without the stall.
    """

    def __init__(self) -> None:
        self._parsers: dict[str, RobotFileParser | None] = {}

    async def allows(self, url: str, user_agent: str = "*") -> bool:
        parsed = urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}"
        if host_key not in self._parsers:
            self._parsers[host_key] = await self._load(host_key)
        parser = self._parsers[host_key]
        if parser is None:
            # No robots.txt, or it was unreachable. The convention is that
            # absent means unrestricted -- refusing here would make every host
            # without a robots.txt uncrawlable.
            return True
        try:
            return bool(parser.can_fetch(user_agent, url))
        except Exception:  # noqa: BLE001 - a malformed rule must not block the crawl
            return True

    async def _load(self, host_key: str) -> RobotFileParser | None:
        try:
            async with httpx.AsyncClient(timeout=_ROBOTS_TIMEOUT_S, follow_redirects=True) as client:
                resp = await client.get(f"{host_key}/robots.txt")
            if resp.status_code >= 400 or not resp.text.strip():
                return None
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
            return parser
        except Exception:  # noqa: BLE001 - unreachable robots.txt means unrestricted
            logger.debug("robots.txt unavailable for %s", host_key, exc_info=True)
            return None


def _record(url: str, *, success: bool, duration_ms: float, error: str | None = None) -> None:
    """One audit record per page, so the panel's counters count pages crawled."""
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


async def crawl_site(
    start_url: str,
    *,
    max_pages: int = 15,
    max_depth: int = 2,
    same_domain: bool = True,
    deadline_s: float = _DEADLINE_S,
) -> dict[str, Any]:
    """Breadth-first crawl. Returns pages, the link graph, and why it stopped.

    Separated from the tool wrapper so tests can assert on structure -- which
    pages were reached, which links were followed, whether robots blocked
    something -- instead of grepping a markdown blob.
    """
    max_pages = max(1, min(int(max_pages), _MAX_PAGES_CEILING))
    max_depth = max(0, min(int(max_depth), _MAX_DEPTH_CEILING))

    started = time.monotonic()
    client = _get_client()
    robots = _RobotsGate()

    seen: set[str] = {start_url}
    frontier: list[str] = [start_url]
    pages: list[dict[str, Any]] = []
    graph: dict[str, list[str]] = {}
    blocked: list[str] = []
    stopped = "completed"

    for depth in range(max_depth + 1):
        if not frontier or len(pages) >= max_pages:
            break
        if time.monotonic() - started > deadline_s:
            stopped = "deadline"
            break

        budget = max_pages - len(pages)
        batch = frontier[:budget]
        frontier = []

        allowed = [u for u, ok in zip(batch, await asyncio.gather(*(robots.allows(u) for u in batch))) if ok]
        blocked.extend(u for u in batch if u not in allowed)
        if not allowed:
            # Every candidate at this depth was disallowed. That is a finished
            # crawl, not a failure -- and it is worth reporting as its own
            # reason so "0 pages" is never mistaken for a broken crawler.
            if not pages:
                stopped = "robots"
            break

        round_started = time.monotonic()
        try:
            results = await client.crawl(allowed, max_pages=len(allowed))
        except Exception as exc:  # noqa: BLE001 - a failed round ends the crawl, not the process
            logger.error("web_crawl round at depth %d failed: %s", depth, exc)
            _record(allowed[0], success=False, duration_ms=(time.monotonic() - round_started) * 1000, error=str(exc)[:200])
            stopped = f"error: {exc}"
            break
        round_ms = (time.monotonic() - round_started) * 1000
        per_page_ms = round_ms / max(len(results), 1)

        for item in results:
            page_url = str(item.get("url") or "")
            if not page_url:
                continue
            text = _page_text(item)
            pages.append({"url": page_url, "depth": depth, "text": text})
            _record(page_url, success=True, duration_ms=per_page_ms)

            links = _links_from(item, page_url)
            graph[page_url] = links
            if depth >= max_depth:
                continue
            for link in links:
                if link in seen:
                    continue
                if same_domain and not _same_site(link, start_url):
                    continue
                seen.add(link)
                frontier.append(link)

    return {
        "start_url": start_url,
        "pages": pages,
        "graph": graph,
        "blocked_by_robots": blocked,
        "stopped": stopped,
        "duration_ms": (time.monotonic() - started) * 1000,
    }


@tool("web_crawl", parse_docstring=True)
async def web_crawl_tool(
    start_url: str,
    max_pages: int = 15,
    max_depth: int = 2,
    same_domain: bool = True,
) -> str:
    """Crawl a site by following links from a starting page.

    Use this when you do not know the URLs yet -- "read the docs for X", "find
    every page on this site that mentions Y". It fetches the start page, follows
    the links it finds, and repeats, breadth-first. robots.txt is respected.

    For URLs you already have, use web_fetch (one) or web_fetch_many (several):
    they are cheaper and return more of each page.

    Args:
        start_url: The page to start from. Must include the scheme (https://example.com).
        max_pages: Total pages to fetch across all depths. Capped at 50.
        max_depth: How many link hops from the start page. 0 is the start page alone. Capped at 3.
        same_domain: Stay on the starting page's host. Turning this off can reach the whole web.
    """
    start_url = (start_url or "").strip()
    if not start_url:
        return "Error: no start_url supplied."
    if urlparse(start_url).scheme not in ("http", "https"):
        return f"Error: start_url must be http(s), got {start_url!r}."

    result = await crawl_site(
        start_url,
        max_pages=max_pages,
        max_depth=max_depth,
        same_domain=same_domain,
    )

    pages = result["pages"]
    if not pages:
        if result["stopped"] == "robots":
            return f"Crawl of {start_url} stopped: robots.txt disallows it."
        if result["stopped"].startswith("error:"):
            return f"Error crawling {start_url}: {result['stopped'][7:]}"
        return f"Crawl of {start_url} returned no readable pages."

    chunks: list[str] = []
    total = 0
    for page in pages:
        text = str(page["text"])[:_PER_PAGE_CHARS].strip()
        if not text:
            continue
        block = f"## {page['url']}\n_depth {page['depth']}_\n\n{text}"
        if total + len(block) > _TOTAL_CHARS:
            chunks.append(f"\n_(truncated — {len(pages) - len(chunks)} more page(s) crawled but not shown)_")
            break
        chunks.append(block)
        total += len(block)

    links_found = sum(len(v) for v in result["graph"].values())
    header = f"Crawled {len(pages)} page(s) from {start_url} (depth {max_depth}, {links_found} link(s) found, stopped: {result['stopped']})"
    if result["blocked_by_robots"]:
        header += f"\n{len(result['blocked_by_robots'])} URL(s) skipped by robots.txt."
    return header + ":\n\n" + "\n\n---\n\n".join(chunks)


__all__ = ["crawl_site", "web_crawl_tool"]
