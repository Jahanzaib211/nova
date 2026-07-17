"""Rate limiting for authentication and cost-sensitive endpoints.

Two independent sliding-window tiers, both keyed by client IP (this middleware
runs before ``AuthMiddleware``, so the authenticated principal is not yet
available):

- **auth tier** — brute-force protection for login / register / change-password.
- **cost tier** — abuse protection for LLM/credit-consuming endpoints (agent
  runs, iGIN0 research, follow-up suggestions). Prevents an attacker from
  spawning unbounded runs and driving provider spend (audit B1). The default
  cap is generous enough that no interactive user reaches it.

Single-node only (in-memory); for multi-node deployments back this with Redis.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

# Auth brute-force tier (suffix match on the auth prefix).
_AUTH_SUFFIXES = ("/auth/login/local", "/auth/register", "/auth/change-password")
_AUTH_WINDOW_SECONDS = 60.0
_AUTH_MAX_ATTEMPTS = 10

# Cost/spend tier: LLM- and credit-consuming endpoints. Both the stateless
# (``/api/runs/stream``) and thread-scoped (``/api/threads/{id}/runs/stream``)
# paths end with these suffixes, so one match covers both.
_COST_SUFFIXES = ("/runs/stream", "/runs/wait", "/research", "/suggestions")

# Workspace indexing walks + AST-parses the whole thread workspace — CPU/IO
# heavy, so it shares the cost tier. Prefix+suffix matched (the generic
# "/index" suffix alone could collide with unrelated endpoints).
_WORKSPACE_SCAN_PREFIX = "/api/workspace/"
_WORKSPACE_SCAN_SUFFIX = "/index"


def _int_env(name: str, default: int) -> int:
    """Read a positive int from env; fall back to ``default`` on absent/invalid."""
    try:
        value = int(os.environ.get(name, ""))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


# Env-overridable so operators can tighten/loosen without a code change.
_COST_WINDOW_SECONDS = float(_int_env("NOVA_RUN_RATE_WINDOW", 60))
_COST_MAX_ATTEMPTS = _int_env("NOVA_RUN_RATE_MAX", 60)


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        # Keyed by ``"{tier}:{ip}"`` so the auth and cost windows are independent.
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _client_ip(self, request: Request) -> str:
        # Honor X-Forwarded-For (nginx) first hop, else peer.
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _classify(self, path: str) -> tuple[str, float, int] | None:
        """Return ``(tier, window_seconds, max_attempts)`` for a throttled path."""
        if any(path.endswith(suffix) for suffix in _AUTH_SUFFIXES):
            return ("auth", _AUTH_WINDOW_SECONDS, _AUTH_MAX_ATTEMPTS)
        if any(path.endswith(suffix) for suffix in _COST_SUFFIXES):
            return ("cost", _COST_WINDOW_SECONDS, _COST_MAX_ATTEMPTS)
        if path.startswith(_WORKSPACE_SCAN_PREFIX) and path.endswith(_WORKSPACE_SCAN_SUFFIX):
            return ("cost", _COST_WINDOW_SECONDS, _COST_MAX_ATTEMPTS)
        return None

    def _rate_limited(self, key: str, now: float, window_seconds: float, max_attempts: int) -> int | None:
        """Record a hit; return ``Retry-After`` seconds when over the limit, else None."""
        window = self._hits[key]
        cutoff = now - window_seconds
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= max_attempts:
            return int(window_seconds - (now - window[0])) + 1

        window.append(now)
        return None

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        classified = self._classify(request.url.path)
        if request.method != "POST" or classified is None:
            return await call_next(request)

        tier, window_seconds, max_attempts = classified
        now = time.monotonic()
        key = f"{tier}:{self._client_ip(request)}"

        retry_after = self._rate_limited(key, now, window_seconds, max_attempts)
        if retry_after is not None:
            message = (
                "Too many attempts. Please wait and try again."
                if tier == "auth"
                else "You're sending requests too quickly. Please wait a moment and try again."
            )
            return JSONResponse(
                status_code=429,
                content={"detail": {"code": "rate_limited", "message": message}},
                headers={"Retry-After": str(max(retry_after, 1))},
            )

        # Opportunistic cleanup so the dict doesn't grow unbounded.
        if len(self._hits) > 10_000:
            global_cutoff = now - max(_AUTH_WINDOW_SECONDS, _COST_WINDOW_SECONDS)
            for k in [k for k, v in self._hits.items() if not v or v[-1] < global_cutoff]:
                self._hits.pop(k, None)

        return await call_next(request)
