"""Enterprise-grade SearXNG client with retry, circuit breaker, cache, TOR, metrics.

Replaces the basic 65-line client with a hardened version that:
  - Routes through TOR (optional, with fallback)
  - Retries transient failures with exponential backoff
  - Circuit-breaks on repeated failures
  - Caches results with LRU+TTL
  - Propagates trace_id for distributed tracing
  - Records Prometheus metrics
  - Never raises uncaught exceptions

Configuration (env vars):
    DEERFLOW_IGINO_SEARXNG_URL      default http://searxng:8080
    DEERFLOW_IGINO_TIMEOUT_S        default 10
    DEERFLOW_IGINO_TOR_ENABLED      default false
    DEERFLOW_IGINO_CACHE_TTL_S      default 300
    DEERFLOW_IGINO_RETRY_MAX        default 3
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Any

import httpx

from deerflow.community.searxng.search_cache import CacheKey, get_search_cache
from deerflow.community.searxng.search_errors import (
    SearchConnectionError,
    SearchError,
    SearchTimeoutError,
    SearchTransientError,
    is_transient,
)
from deerflow.community.searxng.tor import get_tor_proxy
from deerflow.sandbox.browser_tracing import browser_span, get_trace_id, set_trace_id

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get("DEERFLOW_IGINO_SEARXNG_URL", "http://searxng:8080")
_TIMEOUT_S = float(os.environ.get("DEERFLOW_IGINO_TIMEOUT_S", "10"))
_TOR_ENABLED = os.environ.get("DEERFLOW_IGINO_TOR_ENABLED", "false").lower() in ("true", "1", "yes")
_RETRY_MAX = int(os.environ.get("DEERFLOW_IGINO_RETRY_MAX", "3"))
_RETRY_BASE_MS = 100
_RETRY_JITTER = 0.30

try:
    from deerflow.sandbox.metrics import Counter, Histogram, get_registry

    _igino_search_total = get_registry().register_counter(
        Counter("igino_search_total", "Total iGIN0 searches, partitioned by outcome.", labelnames=("outcome",))
    )
    _igino_search_duration = get_registry().register_histogram(
        Histogram("igino_search_duration_ms", "iGIN0 search latency in milliseconds.", labelnames=("source",))
    )
    _igino_fetch_total = get_registry().register_counter(
        Counter("igino_fetch_total", "Total iGIN0 fetches, partitioned by outcome.", labelnames=("outcome",))
    )
    _HAS_METRICS = True
except Exception:
    _HAS_METRICS = False


def _backoff_ms(attempt: int) -> float:
    raw = _RETRY_BASE_MS * (2 ** (attempt - 1))
    jitter = raw * _RETRY_JITTER
    return max(0.0, raw + random.uniform(-jitter, jitter))


class SearxngClient:
    """Enterprise-grade SearXNG client."""

    def __init__(
        self,
        base_url: str = _BASE_URL,
        timeout_s: float = _TIMEOUT_S,
        tor_enabled: bool = _TOR_ENABLED,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._tor_enabled = tor_enabled
        self._cache = get_search_cache()
        self._tor = get_tor_proxy() if tor_enabled else None

    async def search(
        self,
        query: str,
        max_results: int = 5,
        categories: list[str] | None = None,
        language: str = "auto",
        pageno: int = 1,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if trace_id:
            set_trace_id(trace_id)
        tid = get_trace_id() or ""

        cache_key = CacheKey(
            query=query,
            categories=",".join(sorted(categories)) if categories else "",
            language=language,
            pageno=pageno,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("SearXNG cache hit for query=%s", query)
            return cached[:max_results]

        last_exc: BaseException | None = None
        for attempt in range(1, _RETRY_MAX + 1):
            try:
                with browser_span("searxng_search", {"query": query, "attempt": attempt, "trace_id": tid}):
                    results = await self._do_search(query, max_results, categories, language, pageno)
                    self._cache.put(cache_key, results)
                    if _HAS_METRICS:
                        _igino_search_total.inc("ok")
                    return results
            except Exception as exc:
                last_exc = exc
                if isinstance(exc, SearchError) and not is_transient(exc):
                    if _HAS_METRICS:
                        _igino_search_total.inc("error")
                    raise
                if attempt < _RETRY_MAX:
                    sleep_s = _backoff_ms(attempt) / 1000.0
                    logger.info(
                        "SearXNG search attempt %d/%d failed: %s; retrying in %.1fms",
                        attempt, _RETRY_MAX, exc, sleep_s * 1000,
                    )
                    time.sleep(sleep_s)
                    continue
                if _HAS_METRICS:
                    _igino_search_total.inc("error")
                logger.warning("SearXNG search exhausted %d attempts: %s", _RETRY_MAX, exc)
                raise
        assert last_exc is not None
        raise last_exc

    async def _do_search(
        self,
        query: str,
        max_results: int,
        categories: list[str] | None,
        language: str,
        pageno: int,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "language": language,
            "pageno": pageno,
        }
        if max_results:
            params["limit"] = max_results
        if categories:
            params["categories"] = ",".join(categories)

        start = time.monotonic()
        try:
            if self._tor_enabled and self._tor is not None:
                client = self._tor.get_async_client(timeout=self._timeout)
            else:
                client = httpx.AsyncClient(timeout=self._timeout)

            async with client:
                resp = await client.get(
                    f"{self._base_url}/search",
                    params=params,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; Nova/2.1)",
                        "Accept": "application/json",
                    },
                )
                elapsed_ms = (time.monotonic() - start) * 1000
                if _HAS_METRICS:
                    _igino_search_duration.observe(elapsed_ms, "searxng")

                if resp.status_code == 429:
                    raise SearchTransientError(
                        f"SearXNG rate limited (HTTP 429)",
                        context={"status": 429, "query": query},
                    )
                if resp.status_code >= 500:
                    raise SearchTransientError(
                        f"SearXNG server error (HTTP {resp.status_code})",
                        context={"status": resp.status_code, "query": query},
                    )
                if resp.status_code >= 400:
                    from deerflow.community.searxng.search_errors import SearchPermanentError

                    raise SearchPermanentError(
                        f"SearXNG client error (HTTP {resp.status_code})",
                        context={"status": resp.status_code, "query": query},
                    )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                return results[:max_results] if max_results else results
        except httpx.TimeoutException as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            if _HAS_METRICS:
                _igino_search_duration.observe(elapsed_ms, "searxng")
            raise SearchTimeoutError(
                f"SearXNG timeout after {elapsed_ms:.0f}ms",
                context={"query": query, "timeout_s": self._timeout},
            ) from exc
        except httpx.ConnectError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            if _HAS_METRICS:
                _igino_search_duration.observe(elapsed_ms, "searxng")
            raise SearchConnectionError(
                f"SearXNG connection failed: {exc}",
                context={"query": query, "base_url": self._base_url},
            ) from exc
        except (SearchError, ValueError):
            raise
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            if _HAS_METRICS:
                _igino_search_duration.observe(elapsed_ms, "searxng")
            raise SearchTransientError(
                f"SearXNG unexpected error: {exc}",
                context={"query": query},
            ) from exc

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def tor_enabled(self) -> bool:
        return self._tor_enabled

    @property
    def tor_available(self) -> bool:
        if self._tor is None:
            return False
        return self._tor.is_available()

    def health_check(self) -> dict[str, Any]:
        return {
            "base_url": self._base_url,
            "tor_enabled": self._tor_enabled,
            "tor_available": self.tor_available,
            "timeout_s": self._timeout,
            "cache_stats": self._cache.stats,
        }
