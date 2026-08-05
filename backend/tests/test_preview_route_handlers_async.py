"""Regression test for the Batch 3.1 per-method proxy wrappers.

The preview/lpreview/absproxy routes are registered through per-method
wrapper factories so each (path, method) gets a unique operationId. The
wrappers delegate to async implementations — if a wrapper is declared
``def`` instead of ``async def``, FastAPI receives the un-awaited
coroutine as the response value and every preview request 500s with
"TypeError: 'coroutine' object is not iterable" (observed live 2026-07-05).

This test pins the contract: every proxy route handler must be a
coroutine function.
"""

import asyncio

from app.gateway.routers import sandbox as sandbox_router

PROXY_PATH_PREFIXES = (
    "/api/sandbox/preview/",
    "/api/sandbox/lpreview/",
    "/api/sandbox/absproxy/",
    "/api/sandbox/appview/",
)


def test_all_proxy_route_handlers_are_async() -> None:
    routes = [r for r in sandbox_router.router.routes if getattr(r, "path", "").startswith(PROXY_PATH_PREFIXES) and hasattr(r, "endpoint")]
    assert routes, "expected preview/lpreview/absproxy routes to be registered"
    not_async = [f"{r.path} [{','.join(sorted(getattr(r, 'methods', []) or []))}]" for r in routes if not asyncio.iscoroutinefunction(r.endpoint)]
    assert not not_async, f"sync proxy handlers return un-awaited coroutines (500 on every request): {not_async}"
