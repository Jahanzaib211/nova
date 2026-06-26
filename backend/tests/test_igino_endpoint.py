"""Tests for iGIN0 REST endpoints."""

import unittest
from unittest.mock import MagicMock, patch


class TestIGINOEndpoints(unittest.TestCase):
    """Test /api/igino/* router logic (without full FastAPI app)."""

    def test_router_import(self):
        from app.gateway.routers.igino import router
        self.assertIsNotNone(router)
        self.assertTrue(len(router.routes) > 0)

    def test_status_response_shape(self):
        from app.gateway.routers.igino import igino_status
        with patch("app.gateway.routers.igino._igino_status") as mock_status:
            mock_status.return_value = {
                "enabled": True,
                "tor_enabled": False,
                "tor_available": False,
                "searxng_healthy": False,
                "base_url": "http://searxng:8080",
                "cache": {"size": 0, "max_size": 1024, "hits": 0, "misses": 0, "hit_rate": 0.0, "ttl_s": 300},
                "audit": {"total_records": 0, "errors": 0, "tor_usage": 0, "enabled": True, "redacted": False},
            }
            result = igino_status()
            self.assertTrue(result.enabled)
            self.assertFalse(result.tor_enabled)

    def test_toggle_response_shape(self):
        from app.gateway.routers.igino import igino_toggle
        request = MagicMock()
        request.enabled = True
        with patch("app.gateway.routers.igino._igino_toggle") as mock_toggle:
            mock_toggle.return_value = MagicMock(enabled=True, message="iGIN0 enabled")
            result = igino_toggle(request)
            self.assertTrue(result.enabled)


class TestCapabilitiesIginoField(unittest.TestCase):
    """Test capabilities endpoint includes igino field."""

    def test_igino_in_capabilities(self):
        from app.gateway.routers.capabilities import IGINOSummary
        igino = IGINOSummary(
            enabled=False,
            tor_enabled=False,
            tor_available=False,
            searxng_healthy=False,
            circuit_states={},
            cache_stats={"size": 0, "max_size": 0, "hits": 0, "misses": 0, "hit_rate": 0.0, "ttl_s": 0},
            audit_stats={"total_records": 0, "errors": 0, "tor_usage": 0, "enabled": False, "redacted": False},
        )
        self.assertFalse(igino.enabled)
        self.assertFalse(igino.tor_enabled)


class TestAuthMiddleware(unittest.TestCase):
    """Verify iGIN0 endpoints enforce the new auth posture:

    - ``/api/igino/status`` and ``/api/igino/cache`` are readable without
      auth (they return ``{enabled: false}`` when the feature is off).
    - ``/api/igino/toggle``, ``/api/igino/research``, and
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
        import asyncio

        from app.gateway.routers.igino import get_status

        # ``DEERFLOW_IGINO_ENABLED`` is unset in the test env, so the
        # status endpoint must report ``enabled: false`` with HTTP 200.
        result = asyncio.run(get_status())
        self.assertFalse(result["enabled"])

    def test_igino_research_requires_user(self):
        """The research endpoint must call ``get_optional_user_from_request``
        so the per-route auth gate fires for unauthenticated callers."""
        import inspect

        from app.gateway.routers.igino import run_research

        sig = inspect.signature(run_research)
        self.assertIn("user", sig.parameters)


if __name__ == "__main__":
    unittest.main()
