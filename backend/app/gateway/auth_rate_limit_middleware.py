"""Rate limiting for authentication and cost-sensitive endpoints.

Two independent sliding-window tiers, both keyed by client IP (this middleware
runs before ``AuthMiddleware``, so the authenticated principal is not yet
available):

- **auth tier** — brute-force protection for login / register / change-password.
- **cost tier** — abuse protection for LLM/credit-consuming endpoints (agent
  runs, iGIN0 research, follow-up suggestions). Prevents an attacker from
  spawning unbounded runs and driving provider spend (audit B1). The default
  cap is generous enough that no interactive user reaches it.

Backed by ``rate_limiter.py``'s ``RateLimiter`` interface — in-memory
(single replica, the default) or Redis (multi-replica, so an attacker can't
just spread requests across replicas to multiply their effective quota).
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.gateway.rate_limiter import InMemoryRateLimiter, RateLimiter

# Auth brute-force tier (suffix match on the auth prefix).
_AUTH_SUFFIXES = (
    "/auth/login/local",
    "/auth/register",
    "/auth/change-password",
    "/auth/forgot-password",
    "/auth/reset-password",
)
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


def _trusted_proxy_cidrs() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse AUTH_TRUSTED_PROXIES (comma-separated CIDRs) into a tuple."""
    raw = os.environ.get("AUTH_TRUSTED_PROXIES", "")
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            out.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(out)


def _ip_in_trusted_set(ip_str: str, cidrs: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in cidr for cidr in cidrs)


# Env-overridable so operators can tighten/loosen without a code change.
_COST_WINDOW_SECONDS = float(_int_env("NOVA_RUN_RATE_WINDOW", 60))
_COST_MAX_ATTEMPTS = _int_env("NOVA_RUN_RATE_MAX", 60)


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, rate_limiter: RateLimiter | None = None) -> None:
        super().__init__(app)
        # Keyed by "{tier}:{ip}" so the auth and cost windows are independent —
        # one shared limiter instance covers both tiers.
        self._rate_limiter: RateLimiter = rate_limiter or InMemoryRateLimiter()

    def _client_ip(self, request: Request) -> str:
        # Honor X-Forwarded-For (nginx) first hop, else peer.
        xff = request.headers.get("x-forwarded-for")
        peer = request.client.host if request.client else "unknown"
        # Only trust X-Forwarded-For when the TCP peer is in the trusted-proxy
        # CIDR list. Without this guard, any direct client can spoof their IP
        # by setting the header themselves and bypass the rate limiter.
        trusted = _trusted_proxy_cidrs()
        if trusted and xff and _ip_in_trusted_set(peer, trusted):
            return xff.split(",")[0].strip()
        return peer

    def _classify(self, path: str) -> tuple[str, float, int] | None:
        """Return ``(tier, window_seconds, max_attempts)`` for a throttled path."""
        if any(path.endswith(suffix) for suffix in _AUTH_SUFFIXES):
            return ("auth", _AUTH_WINDOW_SECONDS, _AUTH_MAX_ATTEMPTS)
        if any(path.endswith(suffix) for suffix in _COST_SUFFIXES):
            return ("cost", _COST_WINDOW_SECONDS, _COST_MAX_ATTEMPTS)
        if path.startswith(_WORKSPACE_SCAN_PREFIX) and path.endswith(_WORKSPACE_SCAN_SUFFIX):
            return ("cost", _COST_WINDOW_SECONDS, _COST_MAX_ATTEMPTS)
        return None

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        classified = self._classify(request.url.path)
        if request.method != "POST" or classified is None:
            return await call_next(request)

        tier, window_seconds, max_attempts = classified
        key = f"{tier}:{self._client_ip(request)}"

        retry_after = await self._rate_limiter.check(key, window_seconds=window_seconds, max_attempts=max_attempts)
        if retry_after is not None:
            message = "Too many attempts. Please wait and try again." if tier == "auth" else "You're sending requests too quickly. Please wait a moment and try again."
            return JSONResponse(
                status_code=429,
                content={"detail": {"code": "rate_limited", "message": message}},
                headers={"Retry-After": str(max(retry_after, 1))},
            )

        return await call_next(request)
