"""CORS for capability-bearing preview URLs.

A framed preview runs at an opaque origin (``Origin: null``), so to the browser
every request it makes back to its own proxy is cross-origin. Plain
``<link>``/``<script>``/``<img>`` loads do not care, but two kinds of request
do, and both broke a framed Next.js app:

* **Web fonts** are always fetched in CORS mode; without
  ``Access-Control-Allow-Origin`` the browser discards them and the page falls
  back to system fonts (styled, but visibly wrong).
* **``fetch()``** -- Next's client router loads every page with custom headers
  (``RSC``, ``Next-Router-State-Tree``…), which forces a preflight. The
  gateway's own ``CORSMiddleware`` (installed when ``GATEWAY_CORS_ORIGINS`` is
  set) answers a preflight from ``Origin: null`` with 400, so in-app
  navigation failed before the proxy was ever reached.

This layer sits outermost and only acts on paths carrying a preview capability
(``app/gateway/preview_capability.py``). It answers their preflights itself
and adds ``Access-Control-Allow-Origin: *`` to their responses -- never with
credentials, and never for a cookie-authenticated path. That exposes nothing
new: the capability in the URL is already a bearer credential for exactly this
preview, and reading a response cross-origin still requires knowing it.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.gateway.preview_capability import capability_in_path

_PREFLIGHT_MAX_AGE = "600"


class PreviewCapabilityCORSMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or capability_in_path(scope.get("path", "")) is None:
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if scope.get("method") == "OPTIONS" and "access-control-request-method" in headers:
            response_headers = [
                (b"access-control-allow-origin", b"*"),
                (b"access-control-allow-methods", headers["access-control-request-method"].encode("latin-1")),
                (b"access-control-max-age", _PREFLIGHT_MAX_AGE.encode()),
                (b"content-length", b"0"),
            ]
            requested = headers.get("access-control-request-headers")
            if requested:
                response_headers.append((b"access-control-allow-headers", requested.encode("latin-1")))
            await send({"type": "http.response.start", "status": 204, "headers": response_headers})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in (b"access-control-allow-origin", b"access-control-allow-credentials")]
                kept.append((b"access-control-allow-origin", b"*"))
                message = {**message, "headers": kept}
            await send(message)

        await self.app(scope, receive, send_with_cors)
