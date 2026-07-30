"""Regression tests: the CDP browser call sites (workspace_tools.py's
``_cdp_browser_op`` and browser_check.py's ``_run_targets_via_cdp``) must
actually route through the circuit breaker + bounded retry
(browser_circuit_breaker.py / browser_retry.py) when a thread_id is
available.

Both of those modules existed already, fully built and unit-tested in
isolation (test_browser_circuit_breaker.py, test_browser_retry.py) — but
neither was ever wired into a real call site, so every CDP hiccup surfaced
as an immediate, unretried failure regardless. These tests prove the
wiring itself: that a transient failure on the first attempt is retried
and succeeds on the second, that a thread_id is required to get retry
behavior at all (no thread scope -> single unguarded attempt, unchanged
from before), and that the shared exception classifier
(classify_playwright_exception) correctly sorts raw Playwright/network
exceptions into the typed hierarchy retry_browser_call relies on.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from deerflow.sandbox.browser_circuit_breaker import reset_all_circuits
from deerflow.sandbox.browser_errors import (
    BrowserConnectionError,
    BrowserError,
    BrowserTimeoutError,
    classify_playwright_exception,
)


@pytest.fixture(autouse=True)
def _clean_circuits():
    reset_all_circuits()
    yield
    reset_all_circuits()


# ---------------------------------------------------------------------------
# classify_playwright_exception
# ---------------------------------------------------------------------------


class _FakePlaywrightTimeoutError(Exception):
    """Stand-in so tests don't need a real Playwright install to exercise
    the isinstance checks — patched in as playwright.sync_api.TimeoutError."""


class _FakePlaywrightError(Exception):
    """Stand-in for playwright.sync_api.Error."""


def _patch_playwright_error_types():
    return patch.multiple(
        "playwright.sync_api",
        Error=_FakePlaywrightError,
        TimeoutError=_FakePlaywrightTimeoutError,
    )


def test_classify_already_typed_error_passes_through():
    exc = BrowserConnectionError("already classified")
    assert classify_playwright_exception(exc) is exc


def test_classify_playwright_timeout_becomes_browser_timeout_error():
    with _patch_playwright_error_types():
        exc = _FakePlaywrightTimeoutError("Timeout 15000ms exceeded")
        classified = classify_playwright_exception(exc)
    assert isinstance(classified, BrowserTimeoutError)
    assert classified is not exc


def test_classify_connection_error_becomes_browser_connection_error():
    with _patch_playwright_error_types():
        exc = ConnectionError("Connection refused")
        classified = classify_playwright_exception(exc)
    assert isinstance(classified, BrowserConnectionError)


def test_classify_playwright_websocket_message_becomes_browser_connection_error():
    with _patch_playwright_error_types():
        exc = _FakePlaywrightError("websocket error: connection closed")
        classified = classify_playwright_exception(exc)
    assert isinstance(classified, BrowserConnectionError)


def test_classify_playwright_selector_error_left_unclassified():
    """A page-level error (bad selector) is NOT a connection problem — must
    be left unclassified so retry_browser_call fails fast rather than
    retrying something that will never succeed."""
    with _patch_playwright_error_types():
        exc = _FakePlaywrightError("no such element: selector did not match any nodes")
        classified = classify_playwright_exception(exc)
    assert classified is exc
    assert not isinstance(classified, BrowserError)


def test_classify_unknown_exception_left_unclassified():
    exc = ValueError("something unrelated")
    assert classify_playwright_exception(exc) is exc


# ---------------------------------------------------------------------------
# workspace_tools._cdp_browser_op
# ---------------------------------------------------------------------------


def _make_fake_sync_playwright(*, fail_times: int = 0, error_factory=None):
    """Build a fake sync_playwright() context manager whose chromium.connect_over_cdp
    raises ``error_factory()`` for the first ``fail_times`` calls, then succeeds
    and returns a browser that hands back a page satisfying ``op``."""
    call_count = {"n": 0}

    def _sync_playwright():
        pw_cm = MagicMock()

        def _pw_enter():
            pw = MagicMock()

            def _connect_over_cdp(_url, timeout=None):
                call_count["n"] += 1
                if call_count["n"] <= fail_times:
                    raise error_factory()
                browser = MagicMock()
                browser.contexts = []
                ctx = MagicMock()
                browser.new_context.return_value = ctx
                ctx.pages = []
                page = MagicMock()
                ctx.new_page.return_value = page
                return browser

            pw.chromium.connect_over_cdp.side_effect = _connect_over_cdp
            return pw

        pw_cm.__enter__.side_effect = _pw_enter
        pw_cm.__exit__.return_value = False
        return pw_cm

    return _sync_playwright, call_count


def test_cdp_browser_op_without_thread_id_does_not_retry():
    """No thread scope -> single unguarded attempt, identical to the
    pre-wiring behavior. A transient failure must surface immediately."""
    from deerflow.tools.builtins import workspace_tools

    with _patch_playwright_error_types():
        fake_sync_playwright, call_count = _make_fake_sync_playwright(
            fail_times=1,
            error_factory=lambda: ConnectionError("connection refused"),
        )
        with (
            patch("deerflow.sandbox.browser_check._cdp_url_for_gateway", return_value="ws://fake-cdp"),
            patch("playwright.sync_api.sync_playwright", fake_sync_playwright),
        ):
            # The raw ConnectionError is still classified into the typed
            # hierarchy even without a thread_id (classification happens
            # unconditionally in _connect_and_run) — what no thread_id
            # actually changes is that it's never retried, checked below.
            with pytest.raises(BrowserConnectionError):
                workspace_tools._cdp_browser_op(client=MagicMock(), op=lambda page: "ok", thread_id=None)
    assert call_count["n"] == 1, "no thread_id must mean exactly one attempt, no retry"


def test_cdp_browser_op_with_thread_id_retries_transient_failure_and_succeeds():
    """A thread_id present -> a transient connection failure on the first
    attempt is retried and the second attempt succeeds — proving the
    circuit breaker + retry wiring is actually live, not just imported."""
    from deerflow.tools.builtins import workspace_tools

    with _patch_playwright_error_types():
        fake_sync_playwright, call_count = _make_fake_sync_playwright(
            fail_times=1,
            error_factory=lambda: ConnectionError("connection refused"),
        )
        with (
            patch("deerflow.sandbox.browser_check._cdp_url_for_gateway", return_value="ws://fake-cdp"),
            patch("playwright.sync_api.sync_playwright", fake_sync_playwright),
        ):
            result = workspace_tools._cdp_browser_op(client=MagicMock(), op=lambda page: "ok", thread_id="local:test-thread")
    assert result == "ok"
    assert call_count["n"] == 2, "expected exactly one retry after the first transient failure"


def test_cdp_browser_op_with_thread_id_does_not_retry_permanent_error():
    """A page-level error (not connection-related) must fail on the first
    attempt even with a thread_id — retrying a bad selector wastes time."""
    from deerflow.tools.builtins import workspace_tools

    with _patch_playwright_error_types():

        def _op(_page):
            raise _FakePlaywrightError("no such element: selector did not match any nodes")

        fake_sync_playwright, call_count = _make_fake_sync_playwright(fail_times=0, error_factory=lambda: None)
        with (
            patch("deerflow.sandbox.browser_check._cdp_url_for_gateway", return_value="ws://fake-cdp"),
            patch("playwright.sync_api.sync_playwright", fake_sync_playwright),
        ):
            with pytest.raises(_FakePlaywrightError):
                workspace_tools._cdp_browser_op(client=MagicMock(), op=_op, thread_id="local:test-thread")
    assert call_count["n"] == 1, "a permanent (non-connection) error must not be retried"


# ---------------------------------------------------------------------------
# browser_check._run_targets_via_cdp
# ---------------------------------------------------------------------------


def test_run_targets_via_cdp_without_thread_id_does_not_retry():
    from deerflow.sandbox import browser_check

    with _patch_playwright_error_types():
        fake_sync_playwright, call_count = _make_fake_sync_playwright(
            fail_times=1,
            error_factory=lambda: ConnectionError("connection refused"),
        )
        with patch("playwright.sync_api.sync_playwright", fake_sync_playwright):
            with pytest.raises(BrowserConnectionError):
                browser_check._run_targets_via_cdp("ws://fake-cdp", [], with_screenshot=False, render_budget_ms=1000, thread_id=None)
    assert call_count["n"] == 1


def test_run_targets_via_cdp_with_thread_id_retries_connect_failure():
    from deerflow.sandbox import browser_check

    with _patch_playwright_error_types():
        fake_sync_playwright, call_count = _make_fake_sync_playwright(
            fail_times=1,
            error_factory=lambda: ConnectionError("connection refused"),
        )
        with patch("playwright.sync_api.sync_playwright", fake_sync_playwright):
            result = browser_check._run_targets_via_cdp("ws://fake-cdp", [], with_screenshot=False, render_budget_ms=1000, thread_id="local:test-thread")
    assert result == []  # no targets to iterate, just proving the connect succeeded on retry
    assert call_count["n"] == 2
