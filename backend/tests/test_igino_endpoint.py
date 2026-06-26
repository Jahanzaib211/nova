"""Tests for iGIN0 REST endpoints."""

import unittest
from unittest.mock import patch, MagicMock


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
    """Test /api/igino is in public paths."""

    def test_igino_public_path(self):
        from app.gateway.auth_middleware import _PUBLIC_PATH_PREFIXES
        self.assertIn("/api/igino", _PUBLIC_PATH_PREFIXES)


if __name__ == "__main__":
    unittest.main()
