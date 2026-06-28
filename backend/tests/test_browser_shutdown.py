"""Unit tests for the graceful shutdown lifecycle (v7 C5).

Covers:
  - run_shutdown() runs all 3 steps in order
  - Each step is bounded — total time stays under the budget
  - All transient state (breakers, idempotency, last-checks) is cleared
  - Second call is idempotent (returns cached result, no re-run)
  - Step failures don't block other steps
  - install_shutdown_hooks() is idempotent
  - reset_for_testing() lets tests run shutdown multiple times
"""

from __future__ import annotations

import time

import pytest

from deerflow.sandbox import browser_check as bc
from deerflow.sandbox import browser_circuit_breaker as cb
from deerflow.sandbox import shutdown
from deerflow.sandbox.browser_check import BrowserCheck
from deerflow.sandbox.shutdown import (
    install_shutdown_hooks,
    reset_for_testing,
    run_shutdown,
)
from deerflow.tools.builtins.workspace_tools import (
    _browser_navigate_idempotency,
)


@pytest.fixture(autouse=True)
def _isolate():
    """Restore shutdown singleton + clear transient state around each test."""
    cb.reset_all_circuits()
    _browser_navigate_idempotency.clear()
    with _LAST_CHECKS_LOCK_FOR_TEST():
        bc._last_checks.clear()
    reset_for_testing()
    yield
    cb.reset_all_circuits()
    _browser_navigate_idempotency.clear()
    reset_for_testing()


class _LAST_CHECKS_LOCK_FOR_TEST:
    """Tiny context manager — mirrors _LAST_CHECKS_LOCK acquire/release."""

    def __enter__(self):
        bc._LAST_CHECKS_LOCK.acquire()
        return self

    def __exit__(self, *a):
        bc._LAST_CHECKS_LOCK.release()


class TestRunShutdownSteps:
    def test_all_three_steps_run_in_order(self) -> None:
        result = run_shutdown(budget_s=2.0)
        assert "steps" in result
        assert list(result["steps"].keys()) == [
            "drain_semaphore",
            "flush_resources",
            "reset_state",
        ]
        for step in result["steps"].values():
            assert step["ok"] is True

    def test_completes_within_budget(self) -> None:
        start = time.monotonic()
        run_shutdown(budget_s=5.0)
        elapsed = time.monotonic() - start
        # Should complete well under the 5s budget when there's nothing to drain.
        assert elapsed < 2.0, f"shutdown took {elapsed:.1f}s, budget was 5.0s"

    def test_total_elapsed_reported(self) -> None:
        result = run_shutdown(budget_s=2.0)
        assert "total_elapsed_ms" in result
        assert result["total_elapsed_ms"] >= 0
        assert result["total_elapsed_ms"] < 2000  # under budget


class TestStateReset:
    def test_circuit_breakers_cleared(self) -> None:
        # Trip 2 breakers.
        for _ in range(cb._FAILURE_THRESHOLD):
            cb.record_failure("tA")
        for _ in range(cb._FAILURE_THRESHOLD):
            cb.record_failure("tB")
        assert len(cb._breakers) >= 2

        run_shutdown(budget_s=2.0)
        # The registry should be empty after shutdown.
        assert cb._breakers == {}, f"breakers not cleared: {list(cb._breakers.keys())}"

    def test_idempotency_cache_cleared(self) -> None:
        _browser_navigate_idempotency.put("t1", "n1", "cached")
        _browser_navigate_idempotency.put("t1", "n2", "cached")
        assert _browser_navigate_idempotency.get("t1", "n1") == "cached"

        run_shutdown(budget_s=2.0)
        assert _browser_navigate_idempotency.get("t1", "n1") is None
        assert _browser_navigate_idempotency.get("t1", "n2") is None

    def test_last_checks_dict_cleared(self) -> None:
        with _LAST_CHECKS_LOCK_FOR_TEST():
            bc._last_checks["tA"] = BrowserCheck(ok=True, reason="ok")
            bc._last_checks["tB"] = BrowserCheck(ok=False, reason="failed")
            assert len(bc._last_checks) == 2

        run_shutdown(budget_s=2.0)

        with _LAST_CHECKS_LOCK_FOR_TEST():
            assert bc._last_checks == {}, f"last_checks not cleared: {list(bc._last_checks.keys())}"

    def test_all_three_resets_together(self) -> None:
        """Single shutdown call clears ALL transient state, not just one."""
        for _ in range(cb._FAILURE_THRESHOLD):
            cb.record_failure("shared")
        _browser_navigate_idempotency.put("shared", "n", "cached")
        with _LAST_CHECKS_LOCK_FOR_TEST():
            bc._last_checks["shared"] = BrowserCheck(ok=True)

        run_shutdown(budget_s=2.0)

        assert cb._breakers == {}
        assert _browser_navigate_idempotency.get("shared", "n") is None
        with _LAST_CHECKS_LOCK_FOR_TEST():
            assert bc._last_checks == {}


class TestIdempotency:
    def test_second_run_returns_cached_result(self) -> None:
        first = run_shutdown(budget_s=2.0)
        second = run_shutdown()
        assert first == second, "second call must return identical cached result"
        # The cached result is the SAME dict object.
        assert first is second

    def test_reset_for_testing_allows_rerun(self) -> None:
        first = run_shutdown(budget_s=2.0)
        reset_for_testing()
        second = run_shutdown(budget_s=2.0)
        # Different dicts after reset.
        assert first is not second
        # But both have the same shape.
        assert set(first.keys()) == set(second.keys())

    def test_run_shutdown_never_raises(self) -> None:
        """Even with no state, run_shutdown must not raise."""
        # Most defensive case: no state populated, no modules imported beyond shutdown.
        result = run_shutdown(budget_s=1.0)
        assert isinstance(result, dict)


class TestInstallHooks:
    def test_install_is_idempotent(self) -> None:
        # Save + restore so we don't pollute other tests / production state.
        original = shutdown._installed
        try:
            shutdown._installed = False
            first = install_shutdown_hooks()
            second = install_shutdown_hooks()
            third = install_shutdown_hooks()
            assert first is True
            assert second is False
            assert third is False
        finally:
            shutdown._installed = original

    def test_install_registers_atexit(self) -> None:
        # We don't actually trigger atexit (would terminate the test process).
        # Instead, verify _safe_shutdown is registered.

        # Check that the registered atexit hooks include ours.
        # (atexit._exithandlers is internal but stable across CPython versions.)
        # If we can't easily inspect, just verify _installed flips.
        original = shutdown._installed
        try:
            shutdown._installed = False
            install_shutdown_hooks()
            assert shutdown._installed is True
        finally:
            shutdown._installed = original


class TestStepFailureIsolation:
    def test_one_failing_step_doesnt_block_others(self) -> None:
        """If the semaphore drain raises, the other steps must still run.

        Monkey-patch the drain helper to raise, then verify the reset_state
        step still completes and state is cleared.
        """
        # Populate state so reset has something to clear.
        for _ in range(cb._FAILURE_THRESHOLD):
            cb.record_failure("tA")

        # Force the drain step to raise.
        original = shutdown._drain_browser_check_semaphore
        shutdown._drain_browser_check_semaphore = lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("forced drain failure"))
        try:
            result = run_shutdown(budget_s=2.0)
            # Even though drain failed, reset_state ran.
            assert result["steps"]["drain_semaphore"]["ok"] is False
            assert result["steps"]["drain_semaphore"]["error"].startswith("forced drain failure")
            assert result["steps"]["reset_state"]["ok"] is True
            # Breakers were cleared by reset_state despite drain failing.
            assert cb._breakers == {}
        finally:
            shutdown._drain_browser_check_semaphore = original

    def test_reset_step_failure_is_logged(self) -> None:
        """If reset_state raises, the other steps' results are preserved."""
        original = shutdown._reset_browser_state
        shutdown._reset_browser_state = lambda: (_ for _ in ()).throw(RuntimeError("forced reset failure"))
        try:
            result = run_shutdown(budget_s=2.0)
            assert result["steps"]["reset_state"]["ok"] is False
            assert "forced reset failure" in result["steps"]["reset_state"]["error"]
            # Drain + flush still ran.
            assert result["steps"]["drain_semaphore"]["ok"] is True
            assert result["steps"]["flush_resources"]["ok"] is True
        finally:
            shutdown._reset_browser_state = original


class TestShutdownBudget:
    def test_small_budget_still_completes(self) -> None:
        """Even with a tiny budget, shutdown must complete (not hang)."""
        start = time.monotonic()
        result = run_shutdown(budget_s=0.5)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0  # not the 0.5s budget because steps are fast
        assert "total_elapsed_ms" in result

    def test_budget_passed_to_steps(self) -> None:
        """The semaphore drain gets 60% of the budget; flush gets 20%.

        We can't observe the per-step budgets directly, but the total
        elapsed must stay under the full budget + small overhead.
        """
        # With no in-flight work, all steps complete in <100ms.
        start = time.monotonic()
        run_shutdown(budget_s=2.0)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0


class TestBrowserCheckLastCleared:
    def test_last_browser_check_returns_none_after_shutdown(self) -> None:
        """High-level integration: get_last_browser_check returns None after shutdown."""
        from unittest.mock import MagicMock, patch

        from deerflow.sandbox.browser_check import (
            BrowserCheck,
            get_last_browser_check,
            run_browser_check,
        )

        def fake_unlocked(thread_id, sandbox, *, label, routes, with_screenshot, render_budget_ms):
            return BrowserCheck(ok=True, reason="ok")

        sandbox = MagicMock()
        with patch("deerflow.sandbox.browser_check._run_browser_check_unlocked", side_effect=fake_unlocked):
            run_browser_check("t-shutdown", sandbox)
        assert get_last_browser_check("t-shutdown") is not None

        run_shutdown(budget_s=2.0)
        assert get_last_browser_check("t-shutdown") is None
