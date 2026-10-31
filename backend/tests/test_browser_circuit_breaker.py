"""Unit tests for the browser circuit breaker (v7 B2).

Covers the 3-state machine (CLOSED / OPEN / HALF_OPEN), the cooldown
timer, the per-thread isolation, the admin force-override, and the
snapshot/restore helpers used by ops endpoints and tests.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from deerflow.sandbox import browser_circuit_breaker as cb
from deerflow.sandbox.browser_circuit_breaker import (
    CircuitState,
    force_circuit,
    get_circuit_state,
    guard_browser_call,
    record_failure,
    record_success,
    reset_all_circuits,
    snapshot,
)
from deerflow.sandbox.browser_errors import BrowserCircuitOpenError


@pytest.fixture(autouse=True)
def _clean_breakers():
    """Reset all breakers before/after each test for isolation."""
    reset_all_circuits()
    yield
    reset_all_circuits()


class TestStateTransitions:
    """CLOSED -> OPEN after N failures."""

    def test_starts_closed(self) -> None:
        assert get_circuit_state("t") == CircuitState.CLOSED

    def test_one_failure_stays_closed(self) -> None:
        record_failure("t")
        assert get_circuit_state("t") == CircuitState.CLOSED

    def test_threshold_trips_open(self) -> None:
        for _ in range(cb._FAILURE_THRESHOLD):
            record_failure("t")
        assert get_circuit_state("t") == CircuitState.OPEN

    def test_more_than_threshold_still_open(self) -> None:
        for _ in range(cb._FAILURE_THRESHOLD + 5):
            record_failure("t")
        assert get_circuit_state("t") == CircuitState.OPEN


class TestGuardAdmit:
    """OPEN circuit blocks calls; HALF_OPEN admits one probe."""

    def test_closed_admits(self) -> None:
        assert guard_browser_call("t", lambda: 42) == 42

    def test_open_raises_with_cooldown(self) -> None:
        for _ in range(cb._FAILURE_THRESHOLD):
            record_failure("t")
        with pytest.raises(BrowserCircuitOpenError) as exc_info:
            guard_browser_call("t", lambda: 42)
        assert exc_info.value.cooldown_remaining_s > 0
        assert exc_info.value.context["thread_id"] == "t"
        assert "t" in str(exc_info.value)

    def test_half_open_probe_success_closes(self) -> None:
        for _ in range(cb._FAILURE_THRESHOLD):
            record_failure("t")
        # Simulate cooldown elapsed by rewinding opened_at.
        cb._get_breaker("t").opened_at = time.monotonic() - cb._COOLDOWN_S - 1
        # First probe call succeeds -> circuit closes.
        assert guard_browser_call("t", lambda: "ok") == "ok"
        assert get_circuit_state("t") == CircuitState.CLOSED

    def test_half_open_probe_failure_reopens(self) -> None:
        for _ in range(cb._FAILURE_THRESHOLD):
            record_failure("t")
        cb._get_breaker("t").opened_at = time.monotonic() - cb._COOLDOWN_S - 1

        def boom():
            raise RuntimeError("downstream")

        with pytest.raises(RuntimeError):
            guard_browser_call("t", boom)
        assert get_circuit_state("t") == CircuitState.OPEN


class TestSuccessRecording:
    def test_success_in_closed_is_noop(self) -> None:
        record_success("t")
        assert get_circuit_state("t") == CircuitState.CLOSED

    def test_success_in_half_open_closes(self) -> None:
        for _ in range(cb._FAILURE_THRESHOLD):
            record_failure("t")
        cb._get_breaker("t").state = CircuitState.HALF_OPEN
        record_success("t")
        assert get_circuit_state("t") == CircuitState.CLOSED


class TestFailureRecording:
    def test_failures_pruned_after_window(self) -> None:
        """Failures older than the window don't count toward threshold."""
        br = cb._get_breaker("t")
        # Inject failures that are way older than the window.
        br.failures = [time.monotonic() - cb._WINDOW_S - 100] * cb._FAILURE_THRESHOLD
        record_failure("t")
        # Prune removes the old ones; only the new one remains.
        assert len(br.failures) == 1
        assert get_circuit_state("t") == CircuitState.CLOSED


class TestPerThreadIsolation:
    def test_thread_a_trip_does_not_affect_thread_b(self) -> None:
        for _ in range(cb._FAILURE_THRESHOLD):
            record_failure("tA")
        assert get_circuit_state("tA") == CircuitState.OPEN
        assert get_circuit_state("tB") == CircuitState.CLOSED

    def test_guard_returns_value_for_unaffected_thread(self) -> None:
        for _ in range(cb._FAILURE_THRESHOLD):
            record_failure("tA")
        # tB unaffected, call passes through.
        assert guard_browser_call("tB", lambda: 99) == 99


class TestForceCircuit:
    def test_force_open(self) -> None:
        force_circuit("t", CircuitState.OPEN)
        assert get_circuit_state("t") == CircuitState.OPEN

    def test_force_close_clears_failures(self) -> None:
        for _ in range(2):
            record_failure("t")
        force_circuit("t", CircuitState.CLOSED)
        assert get_circuit_state("t") == CircuitState.CLOSED
        assert cb._get_breaker("t").failures == []


class TestSnapshot:
    def test_snapshot_includes_all_known_threads(self) -> None:
        record_failure("tA")
        force_circuit("tA", CircuitState.OPEN)
        force_circuit("tB", CircuitState.CLOSED)
        snap = snapshot()
        assert snap["tA"] == "open"
        assert snap["tB"] == "closed"

    def test_snapshot_can_be_scoped_to_one_thread(self) -> None:
        """The runtime bar is per-chat: another thread's breaker must not show."""
        force_circuit("tA", CircuitState.OPEN)
        force_circuit("tB", CircuitState.OPEN)
        assert snapshot("tA") == {"tA": "open"}
        assert snapshot("tB") == {"tB": "open"}
        assert snapshot("never-seen") == {}

    def test_snapshot_decays_open_to_half_open_after_cooldown(self) -> None:
        """An abandoned OPEN breaker must stop reporting degradation.

        Decay otherwise only happens inside _admit, i.e. when something calls.
        A thread whose Browser tab was closed makes no calls, so its breaker
        stayed "open" forever and painted every surface degraded even though
        the next call would have been admitted as a probe.
        """
        force_circuit("tA", CircuitState.OPEN)
        breaker = cb._get_breaker("tA")
        assert snapshot()["tA"] == "open"

        breaker.opened_at -= cb._COOLDOWN_S + 1
        assert snapshot()["tA"] == "half_open"

    def test_snapshot_decay_does_not_mutate_the_breaker(self) -> None:
        force_circuit("tA", CircuitState.OPEN)
        breaker = cb._get_breaker("tA")
        breaker.opened_at -= cb._COOLDOWN_S + 1
        before = (breaker.state, breaker.opened_at)
        snapshot()
        assert (breaker.state, breaker.opened_at) == before

    def test_snapshot_keeps_closed_and_half_open_unchanged(self) -> None:
        force_circuit("closed-thread", CircuitState.CLOSED)
        force_circuit("probing-thread", CircuitState.HALF_OPEN)
        snap = snapshot()
        assert snap["closed-thread"] == "closed"
        assert snap["probing-thread"] == "half_open"


class TestKwargsAndArgs:
    """guard_browser_call must forward args/kwargs to the protected fn."""

    def test_positional_args(self) -> None:
        def add(a, b):
            return a + b

        assert guard_browser_call("t", add, 2, 3) == 5

    def test_keyword_args(self) -> None:
        def greet(name, *, greeting):
            return f"{greeting}, {name}"

        assert guard_browser_call("t", greet, "world", greeting="hello") == "hello, world"

    def test_returns_value(self) -> None:
        assert guard_browser_call("t", lambda: MagicMock(value="x")) is not None


class TestResetAll:
    def test_reset_clears_every_breaker(self) -> None:
        for tid in ["tA", "tB", "tC"]:
            for _ in range(cb._FAILURE_THRESHOLD):
                record_failure(tid)
        reset_all_circuits()
        snap = snapshot()
        for state in snap.values():
            assert state == "closed"

    def test_reset_clears_failures_when_state_already_closed(self) -> None:
        """Regression: reset_all must clear failures even when state is CLOSED.

        Without this, stale failures from one test pollute the next and
        cause spurious circuit-trips on the first record_failure call.
        """
        # First trip + reset: state goes from OPEN to CLOSED, failures cleared.
        for _ in range(cb._FAILURE_THRESHOLD):
            record_failure("t")
        reset_all_circuits()
        assert get_circuit_state("t") == CircuitState.CLOSED
        assert cb._get_breaker("t").failures == []

        # Now record a single failure in CLOSED state, then reset.
        # The reset MUST clear that single failure.
        record_failure("t")
        assert len(cb._get_breaker("t").failures) == 1
        reset_all_circuits()
        assert cb._get_breaker("t").failures == [], "reset_all_circuits did not clear failures; stale state will pollute subsequent tests"

    def test_repeated_resets_are_idempotent(self) -> None:
        """reset_all can be called many times safely."""
        for _ in range(5):
            record_failure("t")
        for _ in range(5):
            reset_all_circuits()
            assert get_circuit_state("t") == CircuitState.CLOSED
            assert cb._get_breaker("t").failures == []
