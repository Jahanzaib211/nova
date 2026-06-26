"""Tests for iGIN0 TOR proxy wrapper."""

import unittest


class TestTorProxy(unittest.TestCase):

    def _make_proxy(self, **kwargs):
        from deerflow.community.searxng.tor import TorProxy
        return TorProxy(**kwargs)

    def test_default_config(self):
        proxy = self._make_proxy()
        self.assertEqual(proxy.socks5_url, "socks5h://tor:9050")
        self.assertTrue(proxy.fallback_enabled)

    def test_custom_config(self):
        proxy = self._make_proxy(
            socks5_url="socks5h://custom:9051",
            fallback=False,
        )
        self.assertEqual(proxy.socks5_url, "socks5h://custom:9051")
        self.assertFalse(proxy.fallback_enabled)

    def test_fallback_returns_direct_client(self):
        proxy = self._make_proxy(fallback=True)
        client = proxy.get_client(timeout=5.0)
        self.assertIsNotNone(client)

    def test_singleton(self):
        from deerflow.community.searxng.tor import get_tor_proxy
        p1 = get_tor_proxy()
        p2 = get_tor_proxy()
        self.assertIs(p1, p2)


if __name__ == "__main__":
    unittest.main()
