"""Regression tests for the 2026-07 security-hardening audit fixes.

Covers the pure-unit surfaces of the audit changes:
- A4: Stripe redirect allowlist (``billing._safe_redirect``).
- B1: cost-tier rate limiting (``AuthRateLimitMiddleware`` classification + window).

The endpoint-auth fixes (A2 ``require_permission`` on stateless runs, A3
``require_admin_user`` on stream diagnostics) reuse guards already covered by the
authz/deps test suites; these tests pin the new logic introduced by the audit.
"""

from __future__ import annotations

import pytest

from app.gateway.auth_rate_limit_middleware import (
    _COST_MAX_ATTEMPTS,
    AuthRateLimitMiddleware,
)
from app.gateway.routers.billing import _safe_redirect

_ORIGIN = "https://nova.example.com"
_DEFAULT = "https://nova.example.com/workspace?upgraded=1"


# --- A4: Stripe open-redirect allowlist -------------------------------------


def test_safe_redirect_accepts_same_origin_absolute_urls():
    assert _safe_redirect(f"{_ORIGIN}/saas", _ORIGIN, _DEFAULT) == f"{_ORIGIN}/saas"
    assert _safe_redirect(_ORIGIN, _ORIGIN, _DEFAULT) == _ORIGIN


def test_safe_redirect_falls_back_for_unsafe_inputs():
    for bad in (
        "https://evil.com/phish",  # off-origin
        "http://nova.example.com/x",  # scheme mismatch
        "//evil.com",  # protocol-relative
        "/workspace",  # relative (Stripe needs absolute)
        "https://nova.example.com.evil.com/x",  # suffix-confusion host
        None,
        "",
    ):
        assert _safe_redirect(bad, _ORIGIN, _DEFAULT) == _DEFAULT


# --- B1: cost-tier rate limiting --------------------------------------------


def _mw() -> AuthRateLimitMiddleware:
    async def _app(scope, receive, send):  # pragma: no cover - never invoked
        return None

    return AuthRateLimitMiddleware(app=_app)


def test_classify_routes_into_correct_tier():
    mw = _mw()
    assert mw._classify("/api/runs/stream")[0] == "cost"
    assert mw._classify("/api/runs/wait")[0] == "cost"
    assert mw._classify("/api/threads/abc/runs/stream")[0] == "cost"
    assert mw._classify("/api/igino/research")[0] == "cost"
    assert mw._classify("/api/suggestions/threads/abc/suggestions")[0] == "cost"
    assert mw._classify("/api/v1/auth/login/local")[0] == "auth"
    # Non-throttled paths return None.
    assert mw._classify("/api/models") is None
    assert mw._classify("/api/threads/abc/runs") is None


@pytest.mark.anyio
async def test_cost_tier_blocks_after_max_attempts():
    mw = _mw()
    key = "cost:203.0.113.7"
    for _ in range(_COST_MAX_ATTEMPTS):
        assert await mw._rate_limiter.check(key, window_seconds=60.0, max_attempts=_COST_MAX_ATTEMPTS) is None
    retry_after = await mw._rate_limiter.check(key, window_seconds=60.0, max_attempts=_COST_MAX_ATTEMPTS)
    assert retry_after is not None and retry_after >= 1


@pytest.mark.anyio
async def test_windows_are_independent_per_tier_and_ip():
    mw = _mw()
    # Exhaust one IP's cost window.
    for _ in range(_COST_MAX_ATTEMPTS):
        await mw._rate_limiter.check("cost:1.1.1.1", window_seconds=60.0, max_attempts=_COST_MAX_ATTEMPTS)
    assert await mw._rate_limiter.check("cost:1.1.1.1", window_seconds=60.0, max_attempts=_COST_MAX_ATTEMPTS) is not None
    # A different IP is unaffected.
    assert await mw._rate_limiter.check("cost:2.2.2.2", window_seconds=60.0, max_attempts=_COST_MAX_ATTEMPTS) is None
