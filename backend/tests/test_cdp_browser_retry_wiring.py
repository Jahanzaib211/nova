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


def test_cdp_browser_op_does_not_rerun_op_when_close_fails():
    """Regression (caught in review): op() must run at most once, even with
    a thread_id. Before the fix, the whole connect-then-op closure was
    retried as one unit — so a click/fill that succeeded but then hit a
    transient error on browser.close() (which runs in a `finally` AFTER a
    successful `return`, so its exception replaces the return value) would
    get retried as a brand new click/fill. This is a real double-submit
    risk for anything state-mutating, unlike navigate (which has its own
    idempotency cache)."""
    from deerflow.tools.builtins import workspace_tools

    op_call_count = {"n": 0}

    def _op(_page):
        op_call_count["n"] += 1
        return "clicked"

    with _patch_playwright_error_types():

        def _sync_playwright():
            pw_cm = MagicMock()

            def _pw_enter():
                pw = MagicMock()
                browser = MagicMock()
                browser.contexts = []
                ctx = MagicMock()
                browser.new_context.return_value = ctx
                ctx.pages = []
                page = MagicMock()
                ctx.new_page.return_value = page
                pw.chromium.connect_over_cdp.return_value = browser
                # Connect succeeds immediately; close() fails with a
                # connection-shaped error message every time it's called.
                browser.close.side_effect = _FakePlaywrightError("target closed")
                return pw

            pw_cm.__enter__.side_effect = _pw_enter
            pw_cm.__exit__.return_value = False
            return pw_cm

        with (
            patch("deerflow.sandbox.browser_check._cdp_url_for_gateway", return_value="ws://fake-cdp"),
            patch("playwright.sync_api.sync_playwright", _sync_playwright),
        ):
            result = workspace_tools._cdp_browser_op(client=MagicMock(), op=_op, thread_id="local:test-thread")

    assert result == "clicked"
    assert op_call_count["n"] == 1, "op() must run exactly once regardless of a close() failure afterward"


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


def test_run_targets_via_cdp_mid_loop_new_page_failure_does_not_discard_prior_results():
    """Regression (caught in review): page = ctx.new_page() used to sit
    OUTSIDE the per-target try/except, so a failure creating a page for
    ONE target escaped the loop entirely, hit the outer exception handler,
    and (with a thread_id) triggered a full retry that redid every
    already-succeeded target from scratch — contradicting this function's
    own claim that "a bad target never triggers a reconnect". Build 3
    targets where the middle one fails to get a page; the other two must
    still both be recorded, in a single call with no retry."""
    from deerflow.sandbox import browser_check

    targets = [
        ("t1", "html", "<html>one</html>"),
        ("t2", "html", "<html>two</html>"),
        ("t3", "html", "<html>three</html>"),
    ]
    new_page_call_count = {"n": 0}
    connect_call_count = {"n": 0}

    with _patch_playwright_error_types():

        def _sync_playwright():
            pw_cm = MagicMock()

            def _pw_enter():
                pw = MagicMock()

                def _connect_over_cdp(_url, timeout=None):
                    connect_call_count["n"] += 1
                    browser = MagicMock()
                    browser.contexts = []
                    ctx = MagicMock()
                    browser.new_context.return_value = ctx

                    def _new_page():
                        new_page_call_count["n"] += 1
                        if new_page_call_count["n"] == 2:
                            raise _FakePlaywrightError("crashed creating page")
                        page = MagicMock()
                        page.content.return_value = "<html>ok</html>"
                        page.inner_text.return_value = "some text"
                        page.screenshot.return_value = b"fake-png-bytes" * 20
                        return page

                    ctx.new_page.side_effect = _new_page
                    return browser

                pw.chromium.connect_over_cdp.side_effect = _connect_over_cdp
                return pw

            pw_cm.__enter__.side_effect = _pw_enter
            pw_cm.__exit__.return_value = False
            return pw_cm

        with (
            patch.object(browser_check, "_adaptive_wait"),
            patch.object(browser_check, "_scan_html_errors", return_value=None),
            patch("playwright.sync_api.sync_playwright", _sync_playwright),
        ):
            result = browser_check._run_targets_via_cdp("ws://fake-cdp", targets, with_screenshot=True, render_budget_ms=1000, thread_id="local:test-thread")

    assert connect_call_count["n"] == 1, "the middle target's page failure must not trigger a reconnect"
    assert [r.route for r in result] == ["t1", "t2", "t3"], "all three targets must be present, none silently dropped"
    assert result[0].ok is True
    assert result[1].ok is False
    assert result[1].status == "unreachable"
    assert result[2].ok is True
