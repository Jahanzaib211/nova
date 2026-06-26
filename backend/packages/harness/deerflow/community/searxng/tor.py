"""TOR SOCKS5 proxy wrapper with automatic fallback.

Routes HTTP requests through the TOR network for anonymity. When TOR is
unavailable, falls back to direct connections (logged as warning). Never
raises — always returns a working httpx client.

Configuration (env vars):
    DEERFLOW_IGINO_TOR_SOCKS5    default socks5h://tor:9050
    DEERFLOW_IGINO_TOR_FALLBACK  default true

Requires: httpx-socks (pip install httpx-socks)
"""

from __future__ import annotations

import logging
import os
import threading
import time

import httpx

logger = logging.getLogger(__name__)

_SOCKS5_URL = os.environ.get("DEERFLOW_IGINO_TOR_SOCKS5", "socks5h://tor:9050")
_FALLBACK = os.environ.get("DEERFLOW_IGINO_TOR_FALLBACK", "true").lower() in ("true", "1", "yes")

_PROBE_INTERVAL_S = 60.0


class TorProxy:
    """SOCKS5 proxy wrapper for TOR with automatic fallback."""

    def __init__(self, socks5_url: str = _SOCKS5_URL, fallback: bool = _FALLBACK) -> None:
        self._socks5_url = socks5_url
        self._fallback = fallback
        self._available: bool | None = None
        self._probe_at: float = 0.0
        self._lock = threading.Lock()
        self._has_socks = self._check_socks_support()

    @staticmethod
    def _check_socks_support() -> bool:
        try:
            import socks  # noqa: F401
            return True
        except ImportError:
            try:
                from httpx_socks import AsyncProxyTransport  # noqa: F401
                return True
            except ImportError:
                return False

    def is_available(self) -> bool:
        """Check if TOR proxy is reachable (cached for 60s)."""
        if not self._has_socks:
            return False
        now = time.monotonic()
        if self._available is not None and (now - self._probe_at) < _PROBE_INTERVAL_S:
            return self._available
        with self._lock:
            if self._available is not None and (now - self._probe_at) < _PROBE_INTERVAL_S:
                return self._available
            self._probe_at = now
            self._available = self._probe()
            return self._available

    def _probe(self) -> bool:
        try:
            transport = httpx_socks.SyncProxyTransport.from_url(self._socks5_url)
            with httpx.Client(transport=transport, timeout=10.0) as client:
                resp = client.get("https://check.torproject.org/api/ip")
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info("TOR proxy available, exit IP: %s", data.get("IP", "unknown"))
                    return True
                logger.warning("TOR proxy probe returned status %d", resp.status_code)
                return False
        except Exception as exc:
            logger.warning("TOR proxy probe failed: %s", exc)
            return False

    def get_client(self, *, timeout: float = 10.0) -> httpx.Client:
        """Return httpx client routed through TOR, or direct if fallback."""
        if self._has_socks and self.is_available():
            try:
                transport = httpx_socks.SyncProxyTransport.from_url(self._socks5_url)
                return httpx.Client(transport=transport, timeout=timeout)
            except Exception as exc:
                logger.warning("TOR transport creation failed, falling back to direct: %s", exc)
        if self._fallback:
            logger.debug("Using direct connection (TOR unavailable)")
            return httpx.Client(timeout=timeout)
        from deerflow.community.searxng.search_errors import SearchUnavailableError

        raise SearchUnavailableError(
            "TOR proxy unavailable and fallback disabled",
            context={"socks5_url": self._socks5_url},
        )

    def get_async_client(self, *, timeout: float = 10.0) -> httpx.AsyncClient:
        """Return async httpx client routed through TOR, or direct if fallback."""
        if self._has_socks and self.is_available():
            try:
                transport = httpx_socks.AsyncProxyTransport.from_url(self._socks5_url)
                return httpx.AsyncClient(transport=transport, timeout=timeout)
            except Exception as exc:
                logger.warning("TOR async transport creation failed, falling back: %s", exc)
        if self._fallback:
            return httpx.AsyncClient(timeout=timeout)
        from deerflow.community.searxng.search_errors import SearchUnavailableError

        raise SearchUnavailableError(
            "TOR proxy unavailable and fallback disabled",
            context={"socks5_url": self._socks5_url},
        )

    @property
    def socks5_url(self) -> str:
        return self._socks5_url

    @property
    def fallback_enabled(self) -> bool:
        return self._fallback


_proxy: TorProxy | None = None
_proxy_lock = threading.Lock()


def get_tor_proxy() -> TorProxy:
    global _proxy
    if _proxy is not None:
        return _proxy
    with _proxy_lock:
        if _proxy is None:
            _proxy = TorProxy()
        return _proxy


def reset_tor_proxy() -> None:
    global _proxy
    with _proxy_lock:
        _proxy = None
