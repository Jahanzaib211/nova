"""Thin HTTP client for a self-hosted Crawl4AI instance.

Deliberately talks to the REST API rather than importing the `crawl4ai`
package. The library pulls in Playwright and its own browser download, which
would put a second headless-Chrome stack inside the gateway image next to the
Browserless container that already has one. Keeping it behind HTTP means the
crawler is a service we can restart, memory-cap and health-check like SearXNG
and Browserless, instead of a dependency that inflates the gateway.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://crawl4ai:11235"


class Crawl4aiClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, token: str = "", timeout_s: float = 90.0) -> None:
        self._base_url = base_url.rstrip("/")
        # Falls back to the environment so the credential lives in .env beside
        # the one the container itself is given, rather than being duplicated
        # into config.yaml where it would be a secret in a tracked-shaped file.
        self._token = token or os.environ.get("CRAWL4AI_API_TOKEN", "")
        self._timeout_s = timeout_s

    @property
    def base_url(self) -> str:
        return self._base_url

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def health(self) -> bool:
        """Is the instance up? Short timeout so the panel never stalls on it."""
        for path in ("/health", "/"):
            try:
                async with httpx.AsyncClient(timeout=min(self._timeout_s, 5.0)) as client:
                    resp = await client.get(f"{self._base_url}{path}", headers=self._headers())
                if resp.status_code < 400:
                    return True
            except Exception:  # noqa: BLE001 - any failure means "not healthy"
                continue
        return False

    async def crawl(self, urls: list[str], *, max_pages: int = 10) -> list[dict[str, Any]]:
        """Retrieve several pages in one request, as LLM-ready markdown.

        Deliberately does NOT send `deep_crawl_strategy`. Crawl4AI's REST API
        validates every config under an *untrusted* trust boundary and rejects
        that field outright -- "field 'deep_crawl_strategy' is not permitted on
        CrawlerRunConfig from an untrusted request" -- with no server-side
        toggle to opt in. That is a deliberate upstream decision: a link-following
        crawler reachable over HTTP is an open crawler, and an open crawler is an
        SSRF amplifier.

        So this is batch retrieval, not link-following. Link-following would mean
        running Crawl4AI as a library inside the gateway (a trusted context),
        which brings its own Playwright browser download into an image that
        already delegates browsing to the Browserless container.
        """
        payload: dict[str, Any] = {"urls": urls[:max_pages]}

        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.post(f"{self._base_url}/crawl", headers=self._headers(), json=payload)
            resp.raise_for_status()
            body = resp.json()

        results = body.get("results", body if isinstance(body, list) else [])
        return results[:max_pages] if isinstance(results, list) else []
