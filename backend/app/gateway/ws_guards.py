"""Shared WebSocket admission checks.

Extracted from ``routers/sandbox.py`` so the voice transport reuses the exact
same checks rather than growing a second, subtly-different copy. Both must run
**before** ``websocket.accept()`` — once accepted, a rejection is a close frame
the client has to interpret, and any data already sent has been sent.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import WebSocket

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)


async def ws_user(websocket: WebSocket):
    """Authenticate a WebSocket upgrade from its session cookie.

    The global auth middleware is a :class:`BaseHTTPMiddleware`, and Starlette
    skips that class entirely for non-HTTP scope — so a WebSocket handler never
    gets ``request.state.user`` and the user contextvar is never set. Without
    this, every ``caller_owns_thread`` check inside a ws handler resolves to
    ``DEFAULT_USER_ID`` and the socket is rejected for real users.

    Returns the authenticated user (from the ``access_token`` cookie) or
    ``None``. Handlers must ``set_current_user(user)`` around their body so
    downstream ownership checks see the real caller.
    """
    from starlette.requests import Request

    # WebSocket scope is not "http", which Request asserts on. The headers are
    # the handshake headers (including the Cookie header), so a shallow copy
    # with the type patched is enough to read cookies.
    scope = dict(websocket.scope)
    scope["type"] = "http"
    request = Request(scope, websocket.receive, websocket.send)

    from fastapi import HTTPException

    from app.gateway.deps import get_current_user_from_request

    # Deliberately not ``get_optional_user_from_request``: it swallows the
    # HTTPException, and a WebSocket rejection is already opaque (nginx logs
    # 403; the browser only ever says "Unexpected response code: 403"). Losing
    # the reason here makes an expired token indistinguishable from a revoked
    # one in production, which cost days of guessing once already.
    try:
        return await get_current_user_from_request(request)
    except HTTPException as exc:
        detail = exc.detail
        code = detail.get("code") if isinstance(detail, dict) else detail
        logger.warning("ws auth failed: %s", code)
        return None


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


async def ws_caller_owns_thread(websocket: WebSocket, thread_id: str) -> bool:
    """Ownership for a WebSocket, without the fresh-thread race.

    :func:`caller_owns_thread` decides ownership by directory existence, but
    that directory is only materialised when the first *run* starts — while the
    panel opens its socket the moment the chat page mounts. Measured against the
    live deployment, the metadata row landed 16s before the directory did, and
    every socket attempt in that window was refused to the thread's own owner.

    So: keep the directory as the fast path (it needs no database round-trip and
    covers untracked legacy threads), then fall back to the metadata row, which
    is written at thread creation and is the earlier, more authoritative answer.

    ``require_existing=True`` is deliberate and differs from the HTTP routes: a
    missing row is a denial here. Thread ids are guessable, and unlike a REST
    read, a socket that attaches to an id nobody owns yet would keep receiving
    whatever that thread later produces.
    """
    if not thread_id:
        return False

    if caller_owns_thread(thread_id):
        return True
    store = getattr(websocket.app.state, "thread_store", None)
    return await caller_owns_thread_via_store(thread_id, store)


async def caller_owns_thread_via_store(thread_id: str, store) -> bool:
    """The metadata-row half of the ownership answer, transport-agnostic.

    Split out of :func:`ws_caller_owns_thread` so the SSE log stream can use the
    identical fallback. That endpoint had the same fresh-thread race and only
    the WebSocket side was ever fixed: ``/api/sandbox/logs`` still decided
    ownership purely by directory existence, so the Terminal was 404'd for the
    thread's own owner until the first run materialised the directory — and the
    client's geometric backoff then kept the panel blank for another 30s.

    ``require_existing=True`` matches the socket rule: a missing row is a
    denial, because thread ids are guessable.
    """
    if not thread_id or store is None:
        return False
    try:
        user_id = get_effective_user_id()
    except Exception:
        return False
    try:
        return await store.check_access(thread_id, str(user_id), require_existing=True)
    except Exception:
        logger.warning("ownership fallback failed for thread=%s", thread_id, exc_info=True)
        return False


async def reject(websocket: WebSocket, code: int = 1008) -> None:
    """Close before accepting. 1008 = policy violation."""
    try:
        await websocket.close(code=code)
    except Exception:
        pass
