"""Unit tests for the browser health endpoint (v7 C4).

Covers:
  - Probe caching (5s min interval)
  - Open circuit count is accurate
  - Fleet-healthy status (200) vs degraded (503)
  - Circuit states bounded by _MAX_CIRCUIT_STATES_IN_RESPONSE
  - Metrics endpoint renders Prometheus format

All tests use the v7 module-level state (snapshot, reset, record_failure)
for isolation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.gateway.routers import browser_health as bh
from deerflow.sandbox import browser_circuit_breaker as cb


@pytest.fixture(autouse=True)
def _reset():
    cb.reset_all_circuits()
    bh._last_probe.update({"cdp_reachable": None, "latency_ms": None, "last_check_at": 0.0, "cdp_url": None})
    yield
    cb.reset_all_circuits()
    bh._last_probe.update({"cdp_reachable": None, "latency_ms": None, "last_check_at": 0.0, "cdp_url": None})


def _make_test_client():
    """Build a minimal FastAPI app exposing only the health + metrics routes."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(bh.router)
    return TestClient(app)


class TestBrowserHealthShape:
    def test_endpoint_returns_expected_fields(self) -> None:
        client = _make_test_client()
        resp = client.get("/api/health/browser")
        # Even with no circuits, status is healthy (we treat "no circuits known" as healthy).
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "status",
            "cdp_reachable",
            "latency_ms",
            "last_check_at",
            "open_circuits",
            "circuit_states",
            "truncated",
            "total_circuits",
            "reason",
            "cdp_url",
        ):
            assert key in body, f"missing field: {key}"
        assert body["status"] in ("healthy", "degraded")
        assert isinstance(body["circuit_states"], dict)
        # Reset keeps CLOSED breaker entries around (so they can be observed),
        # but no circuits should be OPEN after reset.
        assert body["open_circuits"] == 0
        # All present entries must be CLOSED.
        for state in body["circuit_states"].values():
            assert state == "closed"

    def test_endpoint_returns_200_when_no_circuits_known(self) -> None:
        """An empty breaker registry isn't 'degraded' — we don't have
        evidence the system is broken."""
        client = _make_test_client()
        resp = client.get("/api/health/browser")
        assert resp.status_code == 200

    def test_idle_no_active_thread_sets_reason(self) -> None:
        """When no sandbox is in use, ``cdp_reachable`` is ``None`` (not False)
        and ``reason`` is ``no_active_thread``. Distinguishes the idle state from
        'actively broken' so dashboards / runtime-capability bars stop showing a
        red Browser tab when nothing is using a sandbox.

        Regression for the audit finding where /api/health/browser persistently
        reported ``cdp_reachable: false`` even though no thread was using a
        sandbox — a UX bug, not an outage.
        """
        # Force a real probe run by clearing the cached timestamp.
        bh._last_probe["last_check_at"] = 0.0
        bh._last_probe["cdp_reachable"] = None
        bh._last_probe["latency_ms"] = None
        bh._last_probe["cdp_url"] = None
        with patch.object(bh, "_probe_cdp_once", return_value=(None, None, None)):
            client = _make_test_client()
            resp = client.get("/api/health/browser")
        body = resp.json()
        assert resp.status_code == 200
        assert body["cdp_reachable"] is None
        assert body["reason"] == "no_active_thread"
        assert body["status"] == "healthy"

    def test_probe_failed_sets_reason_and_does_not_503(self) -> None:
        """When a CDP probe ran and failed (refused/timeout), ``reason`` is
        ``probe_failed`` and the status is still 200 unless every circuit is
        OPEN. ``cdp_url`` reports what was probed."""
        bh._last_probe["last_check_at"] = 0.0
        with patch.object(bh, "_probe_cdp_once", return_value=(False, 12.5, "ws://chromium:9222")):
            client = _make_test_client()
            resp = client.get("/api/health/browser")
        body = resp.json()
        assert resp.status_code == 200
        assert body["cdp_reachable"] is False
        assert body["reason"] == "probe_failed"
        assert body["cdp_url"] == "ws://chromium:9222"

    def test_ok_probe_sets_reason(self) -> None:
        bh._last_probe["last_check_at"] = 0.0
        with patch.object(bh, "_probe_cdp_once", return_value=(True, 8.3, "ws://chromium:9222")):
            client = _make_test_client()
            resp = client.get("/api/health/browser")
        body = resp.json()
        assert resp.status_code == 200
        assert body["cdp_reachable"] is True
        assert body["reason"] == "ok"


class TestCircuitCounting:
    def test_open_circuits_count_reflects_breakers(self) -> None:
        # Trip 2 circuits.
        for _ in range(cb._FAILURE_THRESHOLD):
            cb.record_failure("tA")
        for _ in range(cb._FAILURE_THRESHOLD):
            cb.record_failure("tB")
        client = _make_test_client()
        resp = client.get("/api/health/browser")
        body = resp.json()
        assert body["open_circuits"] == 2
        assert body["total_circuits"] == 2
        assert body["circuit_states"]["tA"] == "open"
        assert body["circuit_states"]["tB"] == "open"

    def test_fleet_healthy_with_partial_open(self) -> None:
        """One open circuit doesn't make the whole fleet 'degraded'."""
        for _ in range(cb._FAILURE_THRESHOLD):
            cb.record_failure("tA")
        # tB stays closed
        cb._get_breaker("tB")
        client = _make_test_client()
        resp = client.get("/api/health/browser")
        body = resp.json()
        assert body["status"] == "healthy"
        assert resp.status_code == 200

    def test_fleet_degraded_when_all_open(self) -> None:
        """All known circuits OPEN → 503 degraded."""
        for tid in ["tA", "tB", "tC"]:
            for _ in range(cb._FAILURE_THRESHOLD):
                cb.record_failure(tid)
        client = _make_test_client()
        resp = client.get("/api/health/browser")
        body = resp.json()
        assert body["status"] == "degraded"
        assert resp.status_code == 503


class TestProbeCaching:
    def test_probe_caches_for_min_interval(self) -> None:
        """Calling probe twice within 5s uses the cached result."""
        call_count = {"n": 0}

        def fake_probe():
            call_count["n"] += 1
            return True, 12.3, "ws://chromium:9222"

        with patch.object(bh, "_probe_cdp_once", side_effect=fake_probe):
            # Reset the probe timer so we re-run.
            bh._last_probe["last_check_at"] = 0.0
            client = _make_test_client()
            client.get("/api/health/browser")
            client.get("/api/health/browser")
            client.get("/api/health/browser")
            # Should have probed exactly once due to caching.
            assert call_count["n"] == 1

    def test_probe_refreshes_after_min_interval(self) -> None:
        """After the cache TTL elapses, the probe re-runs."""
        import time

        call_count = {"n": 0}

        def fake_probe():
            call_count["n"] += 1
            return True, 5.0, "ws://chromium:9222"

        with patch.object(bh, "_probe_cdp_once", side_effect=fake_probe):
            client = _make_test_client()
            client.get("/api/health/browser")
            # Backdate the last probe to force a refresh.
            bh._last_probe["last_check_at"] = time.time() - 100
            client.get("/api/health/browser")
            assert call_count["n"] == 2


class TestCircuitStateTruncation:
    def test_response_truncates_circuit_states_when_over_cap(self) -> None:
        """The response caps circuit_states at _MAX_CIRCUIT_STATES_IN_RESPONSE
        and sets truncated=True so dashboards know to fetch more."""
        # Trip more circuits than the cap.
        cap = bh._MAX_CIRCUIT_STATES_IN_RESPONSE
        for i in range(cap + 10):
            tid = f"t{i:04d}"
            for _ in range(cb._FAILURE_THRESHOLD):
                cb.record_failure(tid)
        client = _make_test_client()
        resp = client.get("/api/health/browser")
        body = resp.json()
        assert body["truncated"] is True
        assert len(body["circuit_states"]) == cap
        assert body["total_circuits"] > cap


class TestProbeCdpOnce:
    def test_probe_returns_unreachable_when_no_sandboxes(self) -> None:
        """No registered sandboxes → ``(None, None, None)`` (idle tri-state),
        NOT ``(False, None, None)`` (broken). Distinguishes the two so
        dashboards / runtime capability bars don't flag idle as red.
        """
        with patch("deerflow.sandbox.sandbox_provider.get_sandbox_provider") as mock_provider:
            mock_provider.return_value._sandboxes = {}
            reachable, latency, cdp_url = bh._probe_cdp_once(timeout_s=0.1)
        assert reachable is None
        assert latency is None
        assert cdp_url is None

    def test_probe_handles_provider_exception(self) -> None:
        with patch(
            "deerflow.sandbox.sandbox_provider.get_sandbox_provider",
            side_effect=RuntimeError("provider dead"),
        ):
            reachable, latency, cdp_url = bh._probe_cdp_once(timeout_s=0.1)
        # Provider raised — same tri-state as idle (we have no usable result).
        assert reachable is None
        assert latency is None
        assert cdp_url is None

    def test_probe_handles_sandbox_without_cdp_url(self) -> None:
        sandbox = type("S", (), {"_client": None})()
        provider = type("P", (), {"_sandboxes": {"t1": sandbox}})()
        with patch("deerflow.sandbox.sandbox_provider.get_sandbox_provider", return_value=provider):
            reachable, latency, cdp_url = bh._probe_cdp_once(timeout_s=0.1)
        # Sandbox registered but no usable CDP URL → idle tri-state.
        assert reachable is None
        assert cdp_url is None


class TestMetricsEndpoint:
    def test_metrics_returns_prometheus_text(self) -> None:
        client = _make_test_client()
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        # Content-Type per Prometheus text exposition format.
        assert "text/plain" in resp.headers["content-type"]
        body = resp.text
        # Pre-registered metrics must appear (even with value 0).
        assert "# TYPE browser_check_total counter" in body
        assert "# TYPE browser_check_duration_ms histogram" in body
        assert "# TYPE browser_circuit_state_transitions_total counter" in body

    def test_metrics_increments_visible(self) -> None:
        from deerflow.sandbox.metrics import browser_check_total

        before = browser_check_total.get("ok")
        browser_check_total.inc("ok", n=5)
        client = _make_test_client()
        resp = client.get("/api/metrics")
        # Reset back to original.
        browser_check_total.inc("ok", n=-5)
        after = browser_check_total.get("ok")
        assert resp.status_code == 200
        assert before == after  # We restored correctly
        # The rendered text should reflect the +5 bump.
        assert 'browser_check_total{outcome="ok"}' in resp.text
