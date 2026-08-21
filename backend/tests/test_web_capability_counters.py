"""Every number the Recon panel shows must move when work actually happens.

The panel reported zeros for a long time and nobody could tell whether that
meant "idle" or "broken", because two different things were wrong at once:

  * `searxng_healthy` was the literal `True` -- a light that could not turn red;
  * `AuditTrail.fetch()` existed with no caller, so fetch counters were
    structurally always zero no matter how much fetching happened.

These tests pin the counters to real calls, so a future refactor that stops
recording fails here instead of silently showing an idle-looking panel.
"""

from __future__ import annotations

import pytest

from deerflow.community.searxng.audit import AuditTrail
from deerflow.community.searxng.search_cache import CacheKey, SearchCache


@pytest.fixture
def trail() -> AuditTrail:
    return AuditTrail(enabled=True)


def test_fetch_counters_start_at_zero(trail: AuditTrail) -> None:
    stats = trail.get_stats()
    assert stats["fetches"] == 0
    assert stats["fetch_errors"] == 0
    assert stats["avg_fetch_ms"] == 0.0


def test_fetch_is_counted_separately_from_search(trail: AuditTrail) -> None:
    """A dead crawler and a dead search backend must be distinguishable.

    Both used to land in one `errors` number, which told an operator nothing
    about which half was down.
    """
    trail.search(query="q", sources_searched=["searxng"], results_returned=3, duration_ms=100.0)
    trail.fetch(url="https://example.com", source="browserless", success=True, duration_ms=200.0)

    stats = trail.get_stats()
    assert stats["total_records"] == 2, "both are audited"
    assert stats["fetches"] == 1, "only the fetch counts as a fetch"
    assert stats["fetch_errors"] == 0


def test_failed_fetch_increments_only_fetch_errors(trail: AuditTrail) -> None:
    trail.fetch(url="https://bad.example", source="browserless", success=False, duration_ms=50.0, error="boom")

    stats = trail.get_stats()
    assert stats["fetches"] == 1
    assert stats["fetch_errors"] == 1
    assert stats["errors"] == 1


def test_avg_fetch_ms_is_the_mean_of_real_durations(trail: AuditTrail) -> None:
    trail.fetch(url="https://a.example", source="browserless", success=True, duration_ms=100.0)
    trail.fetch(url="https://b.example", source="crawl4ai", success=True, duration_ms=300.0)

    assert trail.get_stats()["avg_fetch_ms"] == 200.0


def test_crawl_and_fetch_share_the_fetch_counters(trail: AuditTrail) -> None:
    """web_fetch and web_crawl are different jobs but both are page retrieval.

    They are counted together on purpose -- the panel's question is "is
    retrieval working", and splitting it further would make both look idle.
    """
    trail.fetch(url="https://a.example", source="browserless", success=True, duration_ms=10.0)
    trail.fetch(url="https://b.example", source="crawl4ai", success=True, duration_ms=10.0)

    assert trail.get_stats()["fetches"] == 2


def test_cache_counters_move_on_hit_and_miss() -> None:
    """Hit rate is the number that tells you the cache is earning its memory."""
    cache = SearchCache(max_size=8, ttl_s=300)

    assert cache.stats["hits"] == 0 and cache.stats["misses"] == 0

    key = CacheKey(query="query-a", categories="", language="en", pageno=1)

    assert cache.get(key) is None  # miss
    cache.put(key, [{"url": "https://example.com"}])
    assert cache.get(key) is not None  # hit

    stats = cache.stats
    assert stats["misses"] == 1
    assert stats["hits"] == 1
    assert stats["size"] == 1
    assert stats["hit_rate"] == 0.5
