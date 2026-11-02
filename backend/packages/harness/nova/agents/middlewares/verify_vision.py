"""Per-thread store for the latest build-verification screenshot.

The deterministic verify path (``dev_verify`` and the auto-verify gates) captures a
screenshot of the build's current rendered state. This module hands that screenshot
to the vision-capable model via ``ViewImageMiddleware`` so the agent can SEE its
build — blank pages, broken layout, wrong render — not just read console errors.

Design:
- **Vision-gated**: only ``ViewImageMiddleware`` (registered solely for vision models)
  pops from here, so text-only models never receive image content.
- **One-shot**: each screenshot is popped (consumed) on the next model call, so it is
  injected at most once and does not accumulate in conversation history.
- **Bounded**: oversized screenshots are dropped so context never bloats.
- **Non-fatal**: every path is wrapped; a failure here never affects a run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ~3 MB encoded PNG ceiling — a verify screenshot above this is dropped rather
# than injected, so a huge full-page capture can never bloat the model context.
_MAX_SCREENSHOT_B64 = 4_000_000

# thread_id -> {"base64": str, "mime": str}. At most one pending screenshot per
# thread (a newer verify overwrites the older one).
_pending: dict[str, dict[str, str]] = {}


def stash_verify_screenshot(thread_id: str | None, b64: str | None, mime: str = "image/png") -> None:
    """Record the latest verify screenshot for *thread_id* (best-effort, bounded)."""
    try:
        if not thread_id or not b64:
            return
        if len(b64) > _MAX_SCREENSHOT_B64:
            logger.debug("verify screenshot dropped (too large: %d b64 chars)", len(b64))
            return
        _pending[str(thread_id)] = {"base64": b64, "mime": mime}
    except Exception:
        logger.debug("stash_verify_screenshot failed", exc_info=True)


def pop_verify_screenshot(thread_id: str | None) -> dict[str, str] | None:
    """Return and clear the pending verify screenshot for *thread_id*, or None."""
    if not thread_id:
        return None
    try:
        return _pending.pop(str(thread_id), None)
    except Exception:
        return None


def _first_route_screenshot(check: object) -> str | None:
    """Pull the first available raw base64 screenshot from a BrowserCheck result."""
    try:
        for r in getattr(check, "routes", None) or []:
            shot = getattr(r, "screenshot_b64", None)
            if shot:
                return shot
    except Exception:
        logger.debug("_first_route_screenshot failed", exc_info=True)
    return None
