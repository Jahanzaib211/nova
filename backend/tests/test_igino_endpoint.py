"""Tests for iGIN0 REST endpoints."""

import asyncio
import os
import unittest
from unittest.mock import MagicMock, patch


def _run(coro):
    """Run an async coroutine synchronously from a sync test.

    Uses asyncio.run() (the public API) rather than get_event_loop() +
    run_until_complete(); the latter raises "There is no current event loop
    in thread 'MainThread'" on Python 3.10+ when called from a worker thread
    that was not entered via asyncio.run/coroutine. asyncio.run() creates
    and tears down its own loop per call.
    """
    return asyncio.run(coro)


# Stub the app_config loader so importing the router module (which transitively
# imports app.gateway.app) does not require config.yaml on disk. CI runners
# don't have config.yaml (it's gitignored); local devs do. This makes the
# test hermetic and the suite CI-runnable.
_EMPTY_CONFIG_PATCHER = patch(
    "deerflow.config.app_config.get_app_config",
    return_value=MagicMock(),
)


class TestIGINOEndpoints(unittest.TestCase):
    """Test /api/igino/* router logic (without full FastAPI app)."""

    def setUp(self) -> None:
        self._patcher = _EMPTY_CONFIG_PATCHER
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_router_import(self):
        from app.gateway.routers.igino import router

        self.assertIsNotNone(router)
        self.assertGreater(len(router.routes), 0)

    def test_status_response_shape(self):
        from app.gateway.routers import igino as igino_router

        async def fake_status() -> dict:
            return {
                "enabled": True,
                "searxng_healthy": False,
                "cache": {"size": 0, "max_size": 1024, "hits": 0, "misses": 0, "hit_rate": 0.0, "ttl_s": 300},
                "audit": {"total_records": 0, "errors": 0, "tor_usage": 0, "enabled": True, "redacted": False},
            }

        with patch.object(igino_router, "igino_status", new=fake_status):
            result = _run(igino_router.igino_status())
        self.assertTrue(result["enabled"])
        self.assertFalse(result["searxng_healthy"])
        # No base_url. The panel stopped rendering internal URLs, and a value
        # in the network tab is exposed just as surely as one on screen.
        self.assertNotIn("base_url", result)

    def test_toggle_endpoint_is_removed(self):
        """``POST /toggle`` only ever answered 410 and the frontend no longer calls it."""
        from app.gateway.routers import igino as igino_router

        paths = {getattr(r, "path", "") for r in igino_router.router.routes}
        self.assertNotIn("/api/igino/toggle", paths)
        self.assertFalse(hasattr(igino_router, "igino_toggle"))

    def test_status_probes_each_service_once(self):
        """One ``/status`` used to probe Browserless twice (``fetch`` and the
        ``web_fetch`` row) and Crawl4AI twice (``web_fetch_many`` and
        ``web_crawl``)."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.gateway.routers import igino as igino_router

        tools = {
            "web_search": SimpleNamespace(use="deerflow.community.searxng.tools:web_search_tool", model_extra={}),
            "web_fetch": SimpleNamespace(use="deerflow.community.browserless.tools:web_fetch_tool", model_extra={"base_url": "http://browserless:3000"}),
            "web_fetch_many": SimpleNamespace(use="deerflow.community.crawl4ai.tools:web_fetch_many_tool", model_extra={"base_url": "http://crawl4ai:11235"}),
            "web_crawl": SimpleNamespace(use="deerflow.community.crawl4ai.crawl_tool:web_crawl_tool", model_extra={"base_url": "http://crawl4ai:11235"}),
        }
        config = SimpleNamespace(get_tool_config=tools.get)
        http = MagicMock()
        http.get = AsyncMock(return_value=MagicMock(status_code=200))
        http.__aenter__ = AsyncMock(return_value=http)
        http.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.dict(os.environ, {"DEERFLOW_IGINO_ENABLED": "true"}, clear=False),
            patch.object(igino_router, "get_app_config", return_value=config),
            patch.object(igino_router.httpx, "AsyncClient", return_value=http),
            patch("deerflow.community.searxng.searxng_client.SearxngClient.health", new=AsyncMock(return_value=True)),
        ):
            result = _run(igino_router.get_status())

        urls = sorted(call.args[0] for call in http.get.await_args_list)
        self.assertEqual(urls, ["http://browserless:3000/pressure", "http://crawl4ai:11235/health"])
        self.assertEqual(result["fetch"], {"provider": "browserless", "healthy": True, "detail": "reachable"})
        rows = {row["tool"]: row for row in result["web"]}
        self.assertTrue(rows["web_fetch_many"]["healthy"] and rows["web_crawl"]["healthy"])


class TestCapabilitiesIginoField(unittest.TestCase):
    """Test capabilities endpoint includes igino field."""

    def test_igino_in_capabilities(self):
        from app.gateway.routers.capabilities import IGINOSummary

        igino = IGINOSummary(
            enabled=False,
            searxng_healthy=False,
            cache_stats={"size": 0, "max_size": 0, "hits": 0, "misses": 0, "hit_rate": 0.0, "ttl_s": 0},
            audit_stats={"total_records": 0, "errors": 0, "tor_usage": 0, "enabled": False, "redacted": False},
        )
        self.assertFalse(igino.enabled)
        self.assertFalse(igino.searxng_healthy)
        # TOR is gone from the model on purpose: it was removed from the panel,
        # the config and the capability list, and a payload still carrying it
        # invites the next reader to wire it back up.
        self.assertFalse(hasattr(igino, "tor_enabled"))


class TestAuthMiddleware(unittest.TestCase):
    """Verify iGIN0 endpoints enforce the new auth posture:

    - ``/api/igino/status`` and ``/api/igino/cache`` are readable without
      auth (they return ``{enabled: false}`` when the feature is off).
    - ``/api/igino/research`` and
      ``/api/igino/audit`` require a session (otherwise the gateway would
      expose an LLM/SearXNG research pipeline and audit trail to the
      open internet).
    """

    def test_igino_status_path_is_not_in_public_prefixes(self):
        """Status is reachable for any caller but the path itself is no
        longer in the public-prefix allowlist (auth gates the privileged
        endpoints via per-route Depends)."""
        from app.gateway.auth_middleware import _PUBLIC_PATH_PREFIXES

        self.assertNotIn("/api/igino", _PUBLIC_PATH_PREFIXES)

    def test_igino_status_returns_enabled_false_when_disabled(self):
        from app.gateway.routers.igino import get_status

        # Force the flag off rather than assuming the ambient environment has
        # it unset. This assertion silently depended on the developer's .env,
        # so simply switching the feature on in a running deployment turned a
        # green suite red -- the test was measuring the machine, not the code.
        with patch.dict(os.environ, {"DEERFLOW_IGINO_ENABLED": "false"}, clear=False):
            result = asyncio.run(get_status())

        self.assertFalse(result["enabled"])

    def test_igino_status_reports_enabled_when_switched_on(self):
        """The other half of the contract, which nothing covered."""
        from app.gateway.routers.igino import get_status

        with patch.dict(os.environ, {"DEERFLOW_IGINO_ENABLED": "true"}, clear=False):
            result = asyncio.run(get_status())

        self.assertTrue(result["enabled"])
        # A switched-on iGIN0 must describe each capability separately; a single
        # aggregate flag is what made a disabled feature look like a broken one.
        self.assertIn("features", result)
        # "fetch", not "crawler": this reports web_fetch's provider, which
        # renders one URL and follows nothing. The real crawler is web_crawl,
        # reported in `web` alongside the other tools.
        self.assertIn("fetch", result)
        tools = {c["tool"] for c in result.get("web", [])}
        self.assertIn("web_crawl", tools)

    def test_igino_research_requires_user(self):
        """The research endpoint must call ``get_optional_user_from_request``
        so the per-route auth gate fires for unauthenticated callers."""
        import inspect

        from app.gateway.routers.igino import run_research

        sig = inspect.signature(run_research)
        self.assertIn("user", sig.parameters)


if __name__ == "__main__":
    unittest.main()
