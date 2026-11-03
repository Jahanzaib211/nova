import contextlib
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class BrowserlessClient:
    """Client for Browserless headless Chrome API."""

    def __init__(self, base_url: str, token: str = "", timeout_s: float = 30) -> None:
        self.base_url = base_url.rstrip("/")
        # Fall back to the environment, as the crawl4ai client already does.
        # The compose service takes TOKEN=${BROWSERLESS_TOKEN:-}, so setting
        # that variable makes browserless start requiring auth while this
        # client -- which only ever read config.yaml -- kept sending none, and
        # every web_fetch would begin failing with no config change to point at.
        self.token = token or os.environ.get("BROWSERLESS_TOKEN", "")
        self.timeout_s = timeout_s
        # Metadata of the last /content call, for the web_fetch escalation
        # ladder: the *target* page's status and headers (Browserless itself
        # answers 200 and reports the page's own code in X-Response-Code).
        self.last_target_status: int | None = None
        self.last_headers: dict[str, str] = {}

    async def fetch_html(
        self,
        url: str,
        wait_for_event: str = "",
        wait_for_timeout_ms: int = 0,
        wait_for_selector: str = "",
        wait_for_selector_timeout_ms: int = 5000,
        reject_resource_types: list[str] | None = None,
        reject_request_pattern: list[str] | None = None,
        query: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> str:
        """Fetch the rendered HTML of a page using Browserless.

        Only sends accepted parameters for the current Browserless API version.
        Sets a default navigation timeout (30s) via query param.

        Args:
            url: The URL to fetch.
            wait_for_event: Wait for a page event (e.g. "networkidle", "load").
            wait_for_timeout_ms: Extra wait after page load.
            wait_for_selector: CSS selector to wait for.
            wait_for_selector_timeout_ms: Timeout for selector wait.
            reject_resource_types: Resource types to block (e.g. ["image"]).
            reject_request_pattern: URL patterns to block.
            query: Extra query parameters (``launch`` …) from the browser layer.
            extra_body: Extra body fields (``cookies``, ``userAgent`` …) from the
                browser layer. Never logged: it can carry session cookies.

        Returns:
            Rendered HTML content.
        """
        payload: dict[str, Any] = {
            "url": url,
        }

        if self.token:
            payload["token"] = self.token
        if wait_for_event:
            payload["waitForEvent"] = wait_for_event
        if wait_for_timeout_ms > 0:
            payload["waitForTimeout"] = wait_for_timeout_ms
        if wait_for_selector:
            payload["waitForSelector"] = {
                "selector": wait_for_selector,
                "timeout": wait_for_selector_timeout_ms,
            }
        if reject_resource_types:
            payload["rejectResourceTypes"] = reject_resource_types
        if reject_request_pattern:
            payload["rejectRequestPattern"] = reject_request_pattern
        for key, value in (extra_body or {}).items():
            if key == "rejectRequestPattern" and payload.get(key):
                payload[key] = list(payload[key]) + list(value)
            else:
                payload[key] = value
        self.last_target_status = None
        self.last_headers = {}

        logger.debug(f"Fetching URL via Browserless: {url}")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(
                    f"{self.base_url}/content",
                    json=payload,
                    params=dict(query) if query else None,
                    headers={
                        "Content-Type": "application/json",
                        "Cache-Control": "no-cache",
                    },
                )

                code = resp.status_code
                target_code = resp.headers.get("X-Response-Code", "")
                target_status = resp.headers.get("X-Response-Status", "")

                logger.debug(f"Browserless response: code={code}, target_code={target_code}, target_status={target_status}")
                try:
                    self.last_target_status = int(target_code) if target_code else None
                except (TypeError, ValueError):
                    self.last_target_status = None
                with contextlib.suppress(Exception):
                    self.last_headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}

                if code != 200:
                    return f"Error: Browserless HTTP {code}: {resp.text[:200]}"

                html = resp.text
                if not html or not html.strip():
                    return "Error: Browserless returned empty response"

                return html

        except httpx.TimeoutException:
            return f"Error: Browserless request timed out after {self.timeout_s}s"
        except httpx.RequestError as e:
            logger.error(f"Browserless request failed: {e}")
            return f"Error: Browserless request failed: {e!s}"
        except Exception as e:
            logger.error(f"Browserless fetch failed: {e}")
            return f"Error: Browserless fetch failed: {e!s}"
