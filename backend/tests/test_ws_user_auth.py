"""WebSocket admission must authenticate from the session cookie.

The auth middleware is a ``BaseHTTPMiddleware`` and Starlette skips that class
for non-HTTP scope, so WebSocket handlers never get ``request.state.user`` and
the user contextvar is never set. Before ``ws_user`` existed, every ownership
check inside a ws handler resolved to ``DEFAULT_USER_ID`` and real users were
rejected with 1008 — the live terminal/VNC panes and preview HMR socket all
died on production-style deployments.

These tests pin the guard: a valid session cookie authenticates (and the
resulting user feeds the contextvar so downstream checks see the real caller),
while a missing/invalid cookie is rejected.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.gateway import ws_guards
from app.gateway.ws_guards import ws_user
from deerflow.runtime.user_context import get_effective_user_id, reset_current_user, set_current_user


class _FakeWebSocket:
    """Minimal stand-in: only ``scope``/``receive``/``send`` are read by the guard."""

    def __init__(self, headers: list[tuple[bytes, bytes]]):
        self.scope = {
            "type": "websocket",
            "path": "/api/sandbox/appview-ws/thread-1/ws",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 4242),
            "server": ("localhost", 80),
            "scheme": "wss",
        }

    async def receive(self):
        return {"type": "websocket.receive", "text": ""}

    async def send(self, message):
        return None


def _headers(*, cookie: str | None) -> list[tuple[bytes, bytes]]:
    h = [(b"host", b"nova.example")]
    if cookie is not None:
        h.append((b"cookie", cookie.encode()))
    return h


def _spy_auth(monkeypatch, *, fail=False):
    """Replace the auth boundary with a spy that observes the patched Request."""
    seen = {}

    async def fake_get_current_user_from_request(request):
        seen["scope_type"] = request.scope.get("type")
        seen["cookies"] = dict(request.cookies)
        if fail or not seen["cookies"].get("access_token"):
            raise HTTPException(status_code=401)
        return type("User", (), {"id": "user-42"})()

    monkeypatch.setattr("app.gateway.deps.get_current_user_from_request", fake_get_current_user_from_request)
    return seen


class TestWsUser:
    def test_valid_cookie_authenticates(self, monkeypatch) -> None:
        seen = _spy_auth(monkeypatch)
        ws = _FakeWebSocket(_headers(cookie="access_token=abc123; csrf=xyz"))
        user = __import__("asyncio").run(ws_user(ws))
        assert user is not None and user.id == "user-42"
        # The guard must present an http-style Request to the auth boundary so
        # cookie parsing works for the WebSocket upgrade handshake.
        assert seen["scope_type"] == "http"
        assert seen["cookies"].get("access_token") == "abc123"

    def test_missing_cookie_rejected(self, monkeypatch) -> None:
        _spy_auth(monkeypatch)
        ws = _FakeWebSocket(_headers(cookie=None))
        assert __import__("asyncio").run(ws_user(ws)) is None

    def test_invalid_token_rejected(self, monkeypatch) -> None:
        _spy_auth(monkeypatch, fail=True)
        ws = _FakeWebSocket(_headers(cookie="access_token=stale"))
        assert __import__("asyncio").run(ws_user(ws)) is None

    @pytest.mark.no_auto_user
    def test_result_feeds_the_user_contextvar(self, monkeypatch) -> None:
        """Handlers wrap their body in set_current_user(user) so ownership checks
        resolve to the real caller instead of DEFAULT_USER_ID."""
        _spy_auth(monkeypatch)
        ws = _FakeWebSocket(_headers(cookie="access_token=abc123"))
        user = __import__("asyncio").run(ws_user(ws))

        assert get_effective_user_id() == "default"
        token = set_current_user(user)
        try:
            assert get_effective_user_id() == "user-42"
        finally:
            reset_current_user(token)
        assert get_effective_user_id() == "default"


class TestReject:
    def test_reject_before_accept_closes_with_policy_code(self) -> None:
        """Rejection must happen before accept() so the client sees a clean 1008."""
        import asyncio

        messages: list[dict] = []

        class _Ws:
            scope = {"type": "websocket", "headers": [], "query_string": b"", "path": "/", "client": ("x", 1), "server": ("y", 2), "scheme": "ws"}

            async def receive(self):
                return {}

            async def send(self, message):
                messages.append(message)

            async def accept(self):
                raise AssertionError("reject must run before accept")

            async def close(self, code=1000, reason=None):
                messages.append({"type": "websocket.close", "code": code})

        import asyncio as _a

        _a.run(ws_guards.reject(_Ws(), 1008))
        assert messages and messages[-1]["code"] == 1008
