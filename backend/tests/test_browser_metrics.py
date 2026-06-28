"""Unit tests for the in-process metrics module (v7 B5).

Covers Counter / Histogram / Gauge semantics, label-value validation,
Prometheus exposition format, and the pre-registered browser metrics.
"""

from __future__ import annotations

import pytest

from deerflow.sandbox import metrics as m
from deerflow.sandbox.metrics import (
    Counter,
    Gauge,
    Histogram,
    get_registry,
    render_metrics,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Snapshot the registry, clear after each test."""
    yield


class TestCounter:
    def test_inc_increments_by_one(self) -> None:
        c = Counter("c1", "h")
        c.inc()
        c.inc()
        assert c.get() == 2.0

    def test_inc_with_amount(self) -> None:
        c = Counter("c1", "h")
        c.inc(n=5.0)
        assert c.get() == 5.0

    def test_get_unset_returns_zero(self) -> None:
        c = Counter("c1", "h")
        assert c.get() == 0.0

    def test_labelled_counter(self) -> None:
        c = Counter("c2", "h", labelnames=("outcome",))
        c.inc("ok")
        c.inc("ok")
        c.inc("error")
        assert c.get("ok") == 2.0
        assert c.get("error") == 1.0
        assert c.get("never") == 0.0

    def test_wrong_label_count_raises(self) -> None:
        c = Counter("c3", "h", labelnames=("a",))
        with pytest.raises(ValueError):
            c.inc("x", "y")


class TestHistogram:
    def test_observe_increments_count(self) -> None:
        h = Histogram("h1", "h")
        h.observe(10.0)
        h.observe(20.0)
        snap = h.snapshot()
        assert snap[0].count == 2

    def test_observe_accumulates_sum(self) -> None:
        h = Histogram("h1", "h")
        h.observe(10.0)
        h.observe(20.0)
        snap = h.snapshot()
        assert snap[0].sum_ms == 30.0

    def test_buckets_inclusive_upper_bound(self) -> None:
        """value <= bound increments that bucket (Prometheus convention)."""
        h = Histogram("h1", "h", buckets_ms=(10.0, 100.0, 1000.0, float("inf")))
        h.observe(5.0)
        h.observe(50.0)
        h.observe(500.0)
        h.observe(5000.0)
        snap = h.snapshot()[0]
        assert snap.bucket_counts == [1, 2, 3, 4]

    def test_negative_value_ignored(self) -> None:
        h = Histogram("h1", "h")
        h.observe(-5.0)
        # No series was created (no valid observations).
        assert h.snapshot() == []

    def test_nan_value_ignored(self) -> None:
        h = Histogram("h1", "h")
        h.observe(float("nan"))
        assert h.snapshot() == []

    def test_negative_then_valid_observation(self) -> None:
        """Negative observations don't create the series — subsequent valid ones do."""
        h = Histogram("h1", "h")
        h.observe(-5.0)
        h.observe(10.0)
        snap = h.snapshot()
        assert len(snap) == 1
        assert snap[0].count == 1
        assert snap[0].sum_ms == 10.0

    def test_labelled_histogram(self) -> None:
        h = Histogram("h2", "h", labelnames=("engine",))
        h.observe(100.0, "cdp")
        h.observe(50.0, "browser_page")
        snap_by_label = {s.labels: s for s in h.snapshot()}
        assert snap_by_label[(("engine", "cdp"),)].count == 1
        assert snap_by_label[(("engine", "browser_page"),)].count == 1


class TestGauge:
    def test_set(self) -> None:
        g = Gauge("g1", "h")
        g.set(42.0)
        assert g.snapshot()[()] == 42.0

    def test_inc_dec(self) -> None:
        g = Gauge("g1", "h")
        g.set(10.0)
        g.inc(n=5)
        g.dec(n=3)
        assert g.snapshot()[()] == 12.0

    def test_labelled_gauge(self) -> None:
        g = Gauge("g2", "h", labelnames=("region",))
        g.set(1.0, "us-east")
        g.set(2.0, "us-west")
        snap = g.snapshot()
        assert snap[("us-east",)] == 1.0
        assert snap[("us-west",)] == 2.0


class TestRegistry:
    def test_get_registry_singleton(self) -> None:
        a = get_registry()
        b = get_registry()
        assert a is b

    def test_register_returns_same_object(self) -> None:
        c = Counter("x", "h")
        registered = get_registry().register_counter(c)
        assert registered is c

    def test_render_prometheus_format(self) -> None:
        c = get_registry().register_counter(Counter("rt_total", "help", labelnames=("k",)))
        c.inc("v1", n=3)
        text = render_metrics()
        assert "# HELP rt_total help" in text
        assert "# TYPE rt_total counter" in text
        assert 'rt_total{k="v1"} 3.0' in text

    def test_render_histogram_format(self) -> None:
        h = get_registry().register_histogram(Histogram("rt_ms", "h", labelnames=("e",), buckets_ms=(10.0, 100.0, float("inf"))))
        h.observe(5.0, "cdp")
        h.observe(50.0, "cdp")
        text = render_metrics()
        assert "# TYPE rt_ms histogram" in text
        assert 'rt_ms_bucket{e="cdp",le="10.0"} 1' in text
        assert 'rt_ms_bucket{e="cdp",le="100.0"} 2' in text
        # Prometheus convention: +Inf bucket rendered as le="+Inf".
        assert 'rt_ms_bucket{e="cdp",le="+Inf"} 2' in text
        assert 'rt_ms_count{e="cdp"} 2' in text

    def test_render_gauge_format(self) -> None:
        g = get_registry().register_gauge(Gauge("g_val", "h"))
        g.set(99.0)
        text = render_metrics()
        assert "# TYPE g_val gauge" in text
        assert "g_val 99.0" in text

    def test_label_value_escaping(self) -> None:
        c = get_registry().register_counter(Counter("esc_total", "h", labelnames=("k",)))
        c.inc('val with "quote" and \\back\\slash')
        text = render_metrics()
        # backslash and quote must be escaped per Prometheus spec.
        assert '\\"' in text
        assert "\\\\" in text


class TestPreRegisteredMetrics:
    """The pre-registered browser metrics must be accessible and functional."""

    def test_browser_check_total(self) -> None:
        before = m.browser_check_total.get("ok")
        m.browser_check_total.inc("ok")
        after = m.browser_check_total.get("ok")
        assert after == before + 1

    def test_browser_check_duration_observe(self) -> None:
        before = sum(s.count for s in m.browser_check_duration_ms.snapshot())
        m.browser_check_duration_ms.observe(100.0, "cdp")
        after = sum(s.count for s in m.browser_check_duration_ms.snapshot())
        assert after == before + 1

    def test_circuit_state_transitions_inc(self) -> None:
        before = m.circuit_state_transitions_total.get("closed", "open")
        m.circuit_state_transitions_total.inc("closed", "open")
        assert m.circuit_state_transitions_total.get("closed", "open") == before + 1

    def test_circuit_open_count_set(self) -> None:
        m.circuit_open_count.set(7)
        snap = m.circuit_open_count.snapshot()
        assert snap[()] == 7.0

    def test_retry_attempts_total(self) -> None:
        before = m.retry_attempts_total.get("succeeded")
        m.retry_attempts_total.inc("succeeded")
        assert m.retry_attempts_total.get("succeeded") == before + 1

    def test_screenshot_total(self) -> None:
        before = m.screenshot_total.get("ok")
        m.screenshot_total.inc("ok")
        assert m.screenshot_total.get("ok") == before + 1
