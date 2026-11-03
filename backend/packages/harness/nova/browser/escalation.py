"""The web_fetch escalation ladder.

    1. Browserless /content with the configured settings and matching profile cookies
    2. Browserless again with ``launch.stealth=true``          (only after a block signal)
    3. the thread's sandbox Chromium with the user's profile   (only after a block signal,
                                                                and only if the thread
                                                                already has a sandbox)

A rung is climbed only on an explicit block signal from ``challenge.detect_block``
(vendor challenge fingerprints, captcha widgets, 403/429/503 with an empty body)
— never on a slow page or a transport error, which are reported as they are.
At most three attempts, all inside ``browser.escalation.deadline_s``. Each step
is logged (URL redacted, no cookie values) and returned, so the tool result can
say which engine served the page and why it got there.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nova.browser.challenge import BlockSignal, detect_block
from nova.browser.engines import browserless_request
from nova.browser.recording import redact_url
from nova.config.browser_config import BrowserConfig

logger = logging.getLogger(__name__)

SandboxFetch = Callable[[str], Awaitable[dict[str, Any] | None]]


@dataclass
class LadderResult:
    html: str = ""
    engine: str = ""
    error: str = ""
    block: BlockSignal | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        trail = " → ".join(f"{s['engine']}" + (f" [{s['block']}]" if s.get("block") else "") + (" [error]" if s.get("error") else "") for s in self.steps)
        return f"served by {self.engine}" + (f" (ladder: {trail})" if len(self.steps) > 1 else "")


async def fetch_with_ladder(
    url: str,
    client: Any,
    base_kwargs: dict[str, Any],
    cfg: BrowserConfig,
    *,
    cookies: list[dict[str, Any]] | None = None,
    proxy_server: str = "",
    sandbox_fetch: SandboxFetch | None = None,
) -> LadderResult:
    start = time.monotonic()
    deadline = start + cfg.escalation.deadline_s
    result = LadderResult()

    async def browserless(stealth: bool | None, engine: str) -> bool:
        query, body = browserless_request(cfg, stealth=stealth, cookies=cookies, proxy_server=proxy_server)
        t0 = time.monotonic()
        try:
            html = await asyncio.wait_for(client.fetch_html(url=url, query=query or None, extra_body=body or None, **base_kwargs), timeout=max(1.0, deadline - time.monotonic()))
        except TimeoutError:
            html = "Error: ladder deadline reached"
        status = getattr(client, "last_target_status", None)
        status = status if isinstance(status, int) else None
        headers = getattr(client, "last_headers", None)
        headers = headers if isinstance(headers, dict) else {}
        step: dict[str, Any] = {"engine": engine, "status": status, "ms": int((time.monotonic() - t0) * 1000)}
        if html.startswith("Error:"):
            step["error"] = html[:200]
            result.steps.append(step)
            if not result.html:  # a failed later rung keeps the earlier answer
                result.error = html
            return False
        block = detect_block(status, headers, html)
        if block:
            step["block"] = block.kind
        result.steps.append(step)
        result.html, result.engine, result.block, result.error = html, engine, block, ""
        logger.info("web_fetch ladder url=%s engine=%s status=%s block=%s", redact_url(url), engine, status, block.kind if block else None)
        return block is None

    if await browserless(None, "browserless+stealth" if cfg.stealth.enabled else "browserless"):
        return result
    if result.block is None or not cfg.escalation.enabled:
        return result  # a transport error, or the ladder is off: report as-is

    if not cfg.stealth.enabled and time.monotonic() < deadline:
        if await browserless(True, "browserless+stealth"):
            return result

    if sandbox_fetch is not None and cfg.escalation.use_sandbox_browser and time.monotonic() < deadline:
        t0 = time.monotonic()
        try:
            got = await asyncio.wait_for(sandbox_fetch(url), timeout=max(1.0, deadline - time.monotonic()))
        except Exception as exc:  # noqa: BLE001 - the earlier rung's answer still stands
            got = {"error": f"sandbox browser failed: {exc}"}
        step = {"engine": "sandbox", "ms": int((time.monotonic() - t0) * 1000)}
        if got is None:
            step["error"] = "no sandbox in this thread"
            result.steps.append(step)
        elif got.get("error"):
            step["error"] = str(got["error"])[:200]
            result.steps.append(step)
        else:
            block = got.get("block")
            if block:
                step["block"] = block.kind
            result.steps.append(step)
            result.html, result.engine, result.block = str(got.get("html") or ""), "sandbox", block
            logger.info("web_fetch ladder url=%s engine=sandbox block=%s", redact_url(url), block.kind if block else None)
    return result
