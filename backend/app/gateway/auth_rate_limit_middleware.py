"""Rate limiting for authentication endpoints.

Throttles brute-force attempts against login / register / change-password using
an in-memory sliding window keyed by client IP. Single-node only (in-memory);
for multi-node deployments back this with Redis.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

# Paths that are sensitive to brute force (suffix match on the auth prefix).
_PROTECTED_SUFFIXES = ("/auth/login/local", "/auth/register", "/auth/change-password")

# Sliding-window config: max attempts per window per IP.
_WINDOW_SECONDS = 60.0
_MAX_ATTEMPTS = 10


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _client_ip(self, request: Request) -> str:
        # Honor X-Forwarded-For (nginx) first hop, else peer.
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _is_protected(self, path: str) -> bool:
        return any(path.endswith(suffix) for suffix in _PROTECTED_SUFFIXES)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method != "POST" or not self._is_protected(request.url.path):
            return await call_next(request)

        ip = self._client_ip(request)
        now = time.monotonic()
        window = self._hits[ip]

        # Drop entries outside the window.
        cutoff = now - _WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= _MAX_ATTEMPTS:
            retry_after = int(_WINDOW_SECONDS - (now - window[0])) + 1
            return JSONResponse(
                status_code=429,
                content={
                    "detail": {
                        "code": "rate_limited",
                        "message": "Too many attempts. Please wait and try again.",
                    }
                },
                headers={"Retry-After": str(max(retry_after, 1))},
            )

        window.append(now)

        # Opportunistic cleanup so the dict doesn't grow unbounded.
        if len(self._hits) > 10_000:
            for k in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                self._hits.pop(k, None)

        return await call_next(request)
