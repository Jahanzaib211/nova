"""Route a batch between Crawl4AI and Browserless so saved logins work.

Crawl4AI's REST API refuses ``cookies``/``headers``/``storage_state`` from an
untrusted request (HTTP 400, no server-side opt-in), so it can never read a
logged-in page. URLs the user has a browser profile for therefore go to
Browserless ``/content`` with the profile's cookies instead, are converted with
the same readability extractor ``web_fetch`` uses, and come back in Crawl4AI's
item shape (``url``/``markdown``/``html``) so callers cannot tell the
difference. Everything else goes to Crawl4AI with the ``browser:`` settings it
does accept. If the Browserless leg fails, that URL falls back to an anonymous
Crawl4AI fetch rather than disappearing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nova.browser.engines import browserless_request, crawl4ai_configs
from nova.browser.profiles import cookies_for_fetch
from nova.config.browser_config import get_browser_config

logger = logging.getLogger(__name__)

_BROWSERLESS_CONCURRENCY = 4


def fetch_user_id(runtime) -> str | None:
    from nova.runtime.user_context import resolve_runtime_user_id

    try:
        return resolve_runtime_user_id(runtime)
    except Exception:  # noqa: BLE001
        return None


async def _via_browserless(url: str, cookies: list[dict[str, Any]], sem: asyncio.Semaphore) -> dict[str, Any] | None:
    from nova.community.browserless.tools import _get_browserless_client, _readability_extractor

    cfg = get_browser_config()
    query, body = browserless_request(cfg, cookies=cookies)
    async with sem:
        html = await _get_browserless_client().fetch_html(url=url, query=query or None, extra_body=body or None)
    if html.startswith("Error:"):
        logger.info("profile-routed fetch via Browserless failed for a URL; falling back to Crawl4AI: %s", html[:120])
        return None
    article = await asyncio.to_thread(_readability_extractor.extract_article, html)
    return {"url": url, "markdown": article.to_markdown(), "html": html, "served_by": "browserless+profile"}


async def crawl_routed(client: Any, urls: list[str], *, max_pages: int, user_id: str | None, use_profile: bool = True) -> list[dict[str, Any]]:
    urls = urls[:max_pages]
    cookie_map: dict[str, list[dict[str, Any]]] = {}
    if use_profile and user_id:
        for u in urls:
            cookies = await asyncio.to_thread(cookies_for_fetch, u, user_id)
            if cookies:
                cookie_map[u] = cookies

    sem = asyncio.Semaphore(_BROWSERLESS_CONCURRENCY)
    routed_results = await asyncio.gather(*(_via_browserless(u, cookie_map[u], sem) for u in cookie_map))
    by_url: dict[str, dict[str, Any]] = {u: r for u, r in zip(cookie_map, routed_results) if r is not None}

    plain = [u for u in urls if u not in by_url]
    if plain:
        extra = crawl4ai_configs(get_browser_config())
        kwargs: dict[str, Any] = {"max_pages": len(plain)}
        if extra:
            kwargs["extra"] = extra
        for item in await client.crawl(plain, **kwargs):
            u = str(item.get("url") or "")
            by_url.setdefault(u, item)
            if u not in urls:  # a redirect: keep the item, keyed by where it ended up
                urls.append(u)
    return [by_url[u] for u in urls if u in by_url]
