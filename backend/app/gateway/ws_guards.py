"""Shared WebSocket admission checks.

Extracted from ``routers/sandbox.py`` so the voice transport reuses the exact
same checks rather than growing a second, subtly-different copy. Both must run
**before** ``websocket.accept()`` — once accepted, a rejection is a close frame
the client has to interpret, and any data already sent has been sent.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import WebSocket

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id


def caller_owns_thread(thread_id: str) -> bool:
    """Does the calling user own this thread?

    Thread ids are guessable, so without this any authenticated user could
    attach to another user's live session. Ownership is decided by whether the
    thread directory exists *under the caller's own bucket* — the same rule the
    HTTP routes use, so there is one definition of ownership.
    """
    if not thread_id:
        return False
    try:
        user_id = get_effective_user_id()
        return get_paths().thread_dir(thread_id, user_id=user_id).exists()
    except Exception:
        return False


def ws_same_origin(websocket: WebSocket) -> bool:
    """Reject cross-site WebSocket hijacking.

    Browsers do not apply CORS to WebSockets and *do* attach cookies, so a page
    on any origin could otherwise open an authenticated socket. Comparing
    ``Origin`` to ``Host`` is the check that stops it.

    A missing ``Origin`` is allowed: non-browser clients omit it, and they are
    not the threat this defends against — the thread-ownership check still
    applies to them.
    """
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if not origin or not host:
        return True
    try:
        return urlparse(origin).netloc == host
    except ValueError:
        return False


async def reject(websocket: WebSocket, code: int = 1008) -> None:
    """Close before accepting. 1008 = policy violation."""
    try:
        await websocket.close(code=code)
    except Exception:
        pass
