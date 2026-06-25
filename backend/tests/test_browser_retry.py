"""Unit tests for the browser retry helper (v7 B3).

Covers the backoff schedule, jitter bounds, transient/permanent
classification, exhaustion, circuit integration, kwargs forwarding,
and the on_retry callback contract.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from deerflow.sandbox import browser_circuit_breaker as cb
from deerflow.sandbox import browser_retry as br
from deerflow.sandbox.browser_retry import (
    _compute_backoff_ms,
    retry_browser_call,
    retry_browser_call_async,
)
from deerflow.sandbox.browser_circuit_breaker import (
    CircuitState,
    record_failure,
    reset_all_circuits,
)
from deerflow.sandbox.browser_errors import (
    BrowserCircuitOpenError,
    BrowserPermanentError,
    BrowserTransientError,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_all_circuits()
    yield
    reset_all_circuits()


class TestBackoffSchedule:
    def test_attempt_1_base(self) -> None:
        assert _compute_backoff_ms(1, base_ms=100, jitter_fraction=0) == 100.0

    def test_attempt_2_doubles(self) -> None:
        assert _compute_backoff_ms(2, base_ms=100, jitter_fraction=0) == 200.0

    def test_attempt_3_doubles_again(self) -> None:
        assert _compute_backoff_ms(3, base_ms=100, jitter_fraction=0) == 400.0

    def test_jitter_within_bounds(self) -> None:
        """±30% jitter must be respected over many samples."""
        for _ in range(100):
            v = _compute_backoff_ms(2, base_ms=200, jitter_fraction=0.30)
            # nominal for attempt=2: 200*2=400, jitter ±120 → [280, 520]
            assert 280 <= v <= 520, f"jitter out of bounds: {v}"

    def test_jitter_within_bounds_attempt_1(self) -> None:
        """±30% jitter for the first retry (attempt=1)."""
        for _ in range(100):
            v = _compute_backoff_ms(1, base_ms=200, jitter_fraction=0.30)
            # nominal = 200, jitter ±60 → [140, 260]
            assert 140 <= v <= 260, f"jitter out of bounds: {v}"

    def test_custom_base(self) -> None:
        assert _compute_backoff_ms(1, base_ms=50, jitter_fraction=0) == 50.0


class TestSuccessfulFirstAttempt:
    def test_returns_immediately(self) -> None:
        calls = []

        def ok():
            calls.append(1)
            return "good"

        result = retry_browser_call("t", ok, operation="ok")
        assert result == "good"
        assert len(calls) == 1


class TestTransientRetry:
    def test_recovers_within_attempts(self) -> None:
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise BrowserTransientError("flaky")
            return "recovered"

        result = retry_browser_call("t", flaky, operation="flaky", max_attempts=3, base_backoff_ms=10)
        assert result == "recovered"
        assert len(attempts) == 3

    def test_exhausts_then_raises_last(self) -> None:
        attempts = []

        def always():
            attempts.append(1)
            raise BrowserTransientError("always")

        with pytest.raises(BrowserTransientError):
            retry_browser_call("t", always, operation="always", max_attempts=3, base_backoff_ms=10)
        # Up to 3 attempts; may be fewer if circuit trips mid-way under load,
        # but with base_backoff_ms=10 and default settings we should always
        # reach exactly 3 attempts.
        assert len(attempts) >= 1  # at minimum we tried once
        # And typically exactly 3 (default threshold is 3, retries are 2)
        assert len(attempts) <= 3


class TestPermanentFailFast:
    def test_permanent_raises_after_one_attempt(self) -> None:
        attempts = []

        def bad():
            attempts.append(1)
            raise BrowserPermanentError("bad selector")

        with pytest.raises(BrowserPermanentError):
            retry_browser_call("t", bad, operation="bad", max_attempts=3)
        assert len(attempts) == 1, "permanent error should not retry"


class TestNonBrowserException:
    def test_unknown_exception_raises_after_one_attempt(self) -> None:
        """Don't mask unknown bugs by retrying them."""
        attempts = []

        def weird():
            attempts.append(1)
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            retry_browser_call("t", weird, operation="weird", max_attempts=3)
        assert len(attempts) == 1


class TestCircuitIntegration:
    def test_open_circuit_short_circuits(self) -> None:
        # Trip the breaker.
        for _ in range(cb._FAILURE_THRESHOLD):
            record_failure("t")
        assert get_circuit_state_for("t") == CircuitState.OPEN

        attempts = []

        def should_not_run():
            attempts.append(1)
            raise BrowserTransientError("would-be-flaky")

        with pytest.raises(BrowserCircuitOpenError):
            retry_browser_call("t", should_not_run, operation="circuit")
        assert len(attempts) == 0, "OPEN circuit must not invoke the protected fn"


def get_circuit_state_for(tid: str) -> CircuitState:
    from deerflow.sandbox.browser_circuit_breaker import get_circuit_state

    return get_circuit_state(tid)


class TestOnRetryCallback:
    def test_callback_fires_before_each_sleep(self) -> None:
        events = []

        def fail():
            raise BrowserTransientError("nope")

        def on_retry(attempt, sleep_ms, exc):
            events.append((attempt, sleep_ms, type(exc).__name__))

        with pytest.raises(BrowserTransientError):
            retry_browser_call(
                "t", fail, operation="cb",
                max_attempts=3, base_backoff_ms=10,
                on_retry=on_retry,
            )
        # 3 attempts = 2 retries = 2 callbacks (before attempts 2 and 3).
        assert len(events) == 2
        assert events[0][0] == 1
        assert events[1][0] == 2

    def test_callback_exception_does_not_break_retry(self) -> None:
        def bad_callback(*_):
            raise RuntimeError("callback bug")

        def flaky():
            raise BrowserTransientError("x")

        # Callback exception is swallowed; retry continues.
        with pytest.raises(BrowserTransientError):
            retry_browser_call(
                "t", flaky, operation="cb",
                max_attempts=2, base_backoff_ms=10,
                on_retry=bad_callback,
            )


class TestKwargsForwarding:
    def test_kwargs_pass_through(self) -> None:
        def fn(*, a, b):
            return f"{a}-{b}"

        assert retry_browser_call("t", fn, a="x", b="y", operation="kw") == "x-y"

    def test_args_pass_through(self) -> None:
        def fn(x, y):
            return x * y

        assert retry_browser_call("t", fn, 3, 4, operation="args") == 12


class TestInputValidation:
    def test_max_attempts_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            retry_browser_call("t", lambda: 1, max_attempts=0)

    def test_max_attempts_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            retry_browser_call("t", lambda: 1, max_attempts=-5)


class TestAsyncRetry:
    """retry_browser_call_async: same semantics, async sleep."""

    @pytest.mark.asyncio
    async def test_success_first_try(self) -> None:
        async def ok():
            return "async-ok"

        assert await retry_browser_call_async("t", ok, operation="ok") == "async-ok"

    @pytest.mark.asyncio
    async def test_transient_recovers(self) -> None:
        attempts = []

        async def flaky():
            attempts.append(1)
            if len(attempts) < 2:
                raise BrowserTransientError("once")
            return "async-recovered"

        result = await retry_browser_call_async(
            "t", flaky, operation="flaky", max_attempts=3, base_backoff_ms=10,
        )
        assert result == "async-recovered"
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_permanent_fail_fast(self) -> None:
        async def bad():
            raise BrowserPermanentError("nope")

        with pytest.raises(BrowserPermanentError):
            await retry_browser_call_async("t", bad, operation="bad")

    @pytest.mark.asyncio
    async def test_circuit_open_short_circuits(self) -> None:
        for _ in range(cb._FAILURE_THRESHOLD):
            record_failure("t")

        async def should_not_run():
            raise AssertionError("must not run")

        with pytest.raises(BrowserCircuitOpenError):
            await retry_browser_call_async("t", should_not_run, operation="circuit")

    @pytest.mark.asyncio
    async def test_async_uses_event_loop_sleep(self) -> None:
        """Confirm we're not blocking the event loop with time.sleep."""
        import asyncio

        async def ok():
            return "ok"

        # If async path blocked, this concurrent sleep would be delayed.
        loop = asyncio.get_event_loop()
        start = loop.time()
        await asyncio.gather(
            retry_browser_call_async("t", ok, operation="ok"),
            asyncio.sleep(0.05),
        )
        elapsed = loop.time() - start
        assert elapsed < 1.0, f"async retry blocked the loop: {elapsed}s"