"""Path-scoped capabilities for the framed sandbox previews.

The Browser tab frames agent-built apps with an opaque-origin sandbox
(``<iframe sandbox>`` without ``allow-same-origin``) on purpose: an app the agent
wrote must not run as the signed-in user inside Nova's origin. But a request
from an opaque origin is cross-site, so Chromium withholds the ``SameSite=Lax``
session cookie from every stylesheet, script and font the page loads, and the
auth middleware 401'd all of them -- the unstyled Browser tab. (Firefox still
sent the cookie, which is why it rendered there.)

So the credential travels in the URL instead of the cookie jar. The thread
segment of a proxy URL carries a signed capability::

    /api/sandbox/preview/{thread_id}~{user_id}~{expiry}~{signature}/…

The proxies' own URL rewriting keeps that segment on every asset the page
loads, so each request authenticates itself. A capability is bound to one
surface and scope (``preview`` + label, or ``absproxy`` + port), one thread, one
user and that user's ``token_version`` -- a password change or a ban revokes it
-- and it expires. It cannot authenticate any other route: the middleware only
looks for it on these proxy paths.

A capability-bearing path never falls back to the session cookie. If it did, a
forged segment would let a cross-site form ride the cookie past the CSRF
exemption these paths need for the preview app's own POSTs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from dataclasses import dataclass

SEPARATOR = "~"

#: Capabilities are minted on a fixed grid so the URL stays byte-identical for
#: a whole window: the Browser tab keys its iframe on the URL, and a token that
#: changed on every status poll would reload the preview every few seconds.
_WINDOW_S = 12 * 3600
#: Valid for the rest of the minting window plus one more, i.e. 12-24 hours.
_WINDOWS_VALID = 2

_KIND_PREVIEW = "preview"
_KIND_ABSPROXY = "absproxy"

# /api/sandbox/{surface}[-ws]/{segment}/[{label|port}/]…
_PROXY_PATH_RE = re.compile(r"^/api/sandbox/(?P<surface>preview|lpreview|absproxy)(?:-ws)?/(?P<segment>[^/]+)(?:/(?P<scope>[^/]*))?")


@dataclass(frozen=True)
class Capability:
    thread_id: str
    user_id: str
    expiry: int
    signature: str
    kind: str
    scope: str


def _key() -> bytes:
    from app.gateway.auth.config import get_auth_config

    # Domain-separated from the session JWT: the same secret, never the same MAC.
    return hmac.new(get_auth_config().jwt_secret.encode(), b"nova-preview-capability-v1", hashlib.sha256).digest()


def _sign(kind: str, thread_id: str, scope: str, user_id: str, token_version: int, expiry: int) -> str:
    message = f"{kind}|{thread_id}|{scope}|{user_id}|{token_version}|{expiry}".encode()
    digest = hmac.new(_key(), message, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def split_segment(segment: str) -> tuple[str, str | None]:
    """``"{thread}~{cap}"`` → ``("{thread}", "{cap}")``; a bare thread id → ``(id, None)``."""
    thread_id, sep, rest = segment.partition(SEPARATOR)
    return thread_id, (rest if sep else None)


def thread_of(segment: str) -> str:
    """The thread id of a proxy path segment, with any capability removed."""
    return split_segment(segment)[0]


def mint_segment(thread_id: str, kind: str, scope: str, user, *, now: float | None = None) -> str:
    """The thread segment for a proxy URL, carrying a capability for ``user``.

    ``user`` is None (or has no id) when auth is disabled; the bare thread id is
    returned then, since there is no session to stand in for.
    """
    user_id = str(getattr(user, "id", "") or "")
    if not user_id:
        return thread_id
    token_version = int(getattr(user, "token_version", 0) or 0)
    expiry = (int(now if now is not None else time.time()) // _WINDOW_S + _WINDOWS_VALID) * _WINDOW_S
    signature = _sign(kind, thread_id, scope, user_id, token_version, expiry)
    return SEPARATOR.join((thread_id, user_id, str(expiry), signature))


def preview_segment(thread_id: str, label: str, user) -> str:
    return mint_segment(thread_id, _KIND_PREVIEW, label, user)


def absproxy_segment(thread_id: str, port: int | str, user) -> str:
    return mint_segment(thread_id, _KIND_ABSPROXY, str(port), user)


def capability_in_path(path: str) -> Capability | None:
    """Parse the capability out of a proxy path, or ``None`` if it carries none.

    A path that carries something capability-shaped but malformed still returns
    a Capability (with an empty signature) so the caller rejects it rather than
    falling back to the cookie.
    """
    match = _PROXY_PATH_RE.match(path)
    if not match:
        return None
    thread_id, cap = split_segment(match.group("segment"))
    if cap is None:
        return None
    surface = match.group("surface")
    if surface == "preview":
        kind, scope = _KIND_PREVIEW, "app"
    elif surface == "lpreview":
        kind, scope = _KIND_PREVIEW, match.group("scope") or ""
    else:
        kind, scope = _KIND_ABSPROXY, match.group("scope") or ""
    parts = cap.split(SEPARATOR)
    if len(parts) != 3 or not parts[1].isdigit():
        return Capability(thread_id, "", 0, "", kind, scope)
    user_id, expiry, signature = parts
    return Capability(thread_id, user_id, int(expiry), signature, kind, scope)


async def resolve_capability(cap: Capability, *, now: float | None = None):
    """The user a capability authenticates, or ``None`` if it does not verify.

    Checks expiry, then loads the user and recomputes the signature with their
    *current* token_version, so a password change or a ban (both bump it)
    revokes every outstanding capability at once.
    """
    if not cap.signature or not cap.user_id or not cap.thread_id or not cap.scope:
        return None
    if cap.expiry <= int(now if now is not None else time.time()):
        return None

    from app.gateway.deps import get_local_provider

    try:
        user = await get_local_provider().get_user(cap.user_id)
    except Exception:
        return None
    if user is None or getattr(user, "is_forbidden", False):
        return None
    expected = _sign(cap.kind, cap.thread_id, cap.scope, cap.user_id, int(getattr(user, "token_version", 0) or 0), cap.expiry)
    if not hmac.compare_digest(expected, cap.signature):
        return None
    return user
