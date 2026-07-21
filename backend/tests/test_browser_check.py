"""Unit tests for sandbox/browser_check.py.

Covers the public API + private helpers added in v7 determinism sprint:
  - per-thread locks
  - deep-copy return semantics
  - adaptive render wait + clamp
  - URL parsing for CDP netloc rewrite

Plus long-standing helpers that previously had no test coverage:
  - _scan_html_errors, _detect_app_port, _console_entries_to_errors
  - RouteResult.to_dict, BrowserCheck.summary

Pattern: follow test_browserless_client.py style — direct module import,
MagicMock for sandbox objects, no Playwright fixture needed for unit tests
of helpers. End-to-end CDP tests are kept in the existing test_dev_server_*
suite (already covers the live path).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from deerflow.sandbox import browser_check as bc
from deerflow.sandbox.browser_check import (
    _DEFAULT_RENDER_BUDGET_MS,
    _LAST_CHECKS_LOCK,
    _MAX_RENDER_BUDGET_MS,
    BrowserCheck,
    RouteResult,
    _adaptive_wait,
    _cdp_url_for_gateway,
    _clamp_render_budget_ms,
    _console_entries_to_errors,
    _detect_app_port,
    _get_thread_lock,
    _rewrite_cdp_netloc,
    _run_browser_check_unlocked,
    _scan_html_errors,
    get_last_browser_check,
    run_browser_check,
)

# ============================================================
# _scan_html_errors
# ============================================================


class TestScanHtmlErrors:
    """All explicit markers must be caught; non-markers must NOT raise."""

    @pytest.mark.parametrize(
        "marker",
        [
            "Cannot find module",
            "Module not found",
            "Unhandled Runtime Error",
            "Application error: a client-side exception",
            "__next_error__",
            "vite-error-overlay",
            "Internal Server Error",
            "ECONNREFUSED",
            "This site can\u2019t be reached",  # curly apostrophe variant
            "This site can't be reached",  # straight apostrophe variant
        ],
    )
    def test_markers_caught(self, marker: str) -> None:
        html = f"<html><body>oops: {marker} happened</body></html>"
        assert _scan_html_errors(html) == marker

    def test_clean_html_returns_none(self) -> None:
        assert _scan_html_errors("<html><body>hello world</body></html>") is None

    def test_empty_html_returns_none(self) -> None:
        assert _scan_html_errors("") is None

    def test_marker_in_attribute_caught(self) -> None:
        # markers should match anywhere, including attribute values.
        html = '<div data-error="Cannot find module">x</div>'
        assert _scan_html_errors(html) == "Cannot find module"


# ============================================================
# _detect_app_port
# ============================================================


class TestDetectAppPort:
    """Port discovery from ss/netstat output."""

    def test_preferred_port_in_listening_set(self) -> None:
        sandbox = MagicMock()
        sandbox.execute_command.return_value = "LISTEN 0 128 *:3000 *:*"
        assert _detect_app_port(sandbox, prefer=3000) == 3000

    def test_preferred_port_over_common_when_listening(self) -> None:
        sandbox = MagicMock()
        sandbox.execute_command.return_value = "LISTEN 0 128 *:5173 *:*\nLISTEN 0 128 *:3000 *:*"
        # prefer=3000 should win even though 5173 is the first common-dev match
        assert _detect_app_port(sandbox, prefer=3000) == 3000

    def test_common_dev_port_fallback(self) -> None:
        sandbox = MagicMock()
        sandbox.execute_command.return_value = "LISTEN 0 128 *:5173 *:*"
        assert _detect_app_port(sandbox, prefer=9999) == 5173

    def test_sandbox_ports_excluded(self) -> None:
        sandbox = MagicMock()
        # 8080 is in _SANDBOX_PORTS — must NOT be chosen as the app port.
        sandbox.execute_command.return_value = "LISTEN 0 128 *:8080 *:*"
        # Falls back to prefer since no candidates remain.
        assert _detect_app_port(sandbox, prefer=4100) == 4100

    def test_execute_command_raises_returns_prefer(self) -> None:
        sandbox = MagicMock()
        sandbox.execute_command.side_effect = RuntimeError("sandbox down")
        assert _detect_app_port(sandbox, prefer=4100) == 4100

    def test_ipv6_listen_line(self) -> None:
        sandbox = MagicMock()
        sandbox.execute_command.return_value = "LISTEN 0 128 [::]:3000 [::]:*"
        assert _detect_app_port(sandbox, prefer=3000) == 3000

    def test_multiple_candidates_pick_smallest(self) -> None:
        sandbox = MagicMock()
        sandbox.execute_command.return_value = "LISTEN 0 128 *:8765 *:*\nLISTEN 0 128 *:4321 *:*\n"
        # Neither in _COMMON_DEV_PORTS; prefer is not listening; pick smallest.
        assert _detect_app_port(sandbox, prefer=9999) == 4321

    def test_no_listening_returns_prefer(self) -> None:
        sandbox = MagicMock()
        sandbox.execute_command.return_value = ""
        assert _detect_app_port(sandbox, prefer=4100) == 4100

    def test_jupyter_port_8888_never_wins_over_real_app(self) -> None:
        # AIO sandbox runs jupyter-lab on 8888 (its own shell UI). When the
        # assigned port is stale and both jupyter and the real app are
        # listening, the verifier must pick the app — probing jupyter
        # produced spurious 404 verdicts on healthy apps (live incident
        # 2026-07-19, thread 53ecf178).
        sandbox = MagicMock()
        sandbox.execute_command.return_value = "LISTEN 0 128 *:8888 *:*\nLISTEN 0 128 *:4101 *:*"
        assert _detect_app_port(sandbox, prefer=3000) == 4101

    def test_jupyter_port_8888_alone_falls_back_to_prefer(self) -> None:
        # Only jupyter listening -> no app candidates at all; keep prefer.
        sandbox = MagicMock()
        sandbox.execute_command.return_value = "LISTEN 0 128 *:8888 *:*"
        assert _detect_app_port(sandbox, prefer=4100) == 4100


# ============================================================
# _rewrite_cdp_netloc
# ============================================================


class TestRewriteCdpNetloc:
    """urllib.parse-based host swap with port/path/query/fragment preservation."""

    def test_localhost_swap(self) -> None:
        out = _rewrite_cdp_netloc("ws://localhost:8080/devtools/browser/abc", "host.docker.internal")
        assert out == "ws://host.docker.internal:8080/devtools/browser/abc"

    def test_ipv4_loopback_swap(self) -> None:
        out = _rewrite_cdp_netloc("ws://127.0.0.1:9222/devtools/page/xyz", "host.docker.internal")
        assert out == "ws://host.docker.internal:9222/devtools/page/xyz"

    def test_ipv6_swap(self) -> None:
        out = _rewrite_cdp_netloc("ws://[::1]:8080/path", "host.docker.internal")
        assert out == "ws://host.docker.internal:8080/path"

    def test_query_and_fragment_preserved(self) -> None:
        out = _rewrite_cdp_netloc("ws://localhost:8080/path?x=1&y=2#frag", "host.docker.internal")
        assert out == "ws://host.docker.internal:8080/path?x=1&y=2#frag"

    def test_no_port(self) -> None:
        out = _rewrite_cdp_netloc("ws://localhost/path", "host.docker.internal")
        assert out == "ws://host.docker.internal/path"

    def test_custom_scheme(self) -> None:
        out = _rewrite_cdp_netloc("http://localhost:3000/healthz", "host.docker.internal")
        assert out == "http://host.docker.internal:3000/healthz"

    def test_unparseable_returns_none(self) -> None:
        # Empty scheme makes urlsplit not parse correctly.
        assert _rewrite_cdp_netloc("", "h") is None
        # Pure garbage
        assert _rewrite_cdp_netloc("not a url at all", "h") is None

    def test_no_scheme_returns_none(self) -> None:
        # urlsplit accepts scheme-less URLs but we treat them as invalid
        # (a CDP URL must have a scheme).
        assert _rewrite_cdp_netloc("localhost:8080/x", "h") is None


# ============================================================
# _cdp_url_for_gateway
# ============================================================


class TestCdpUrlForGateway:
    """Top-level helper that gates on the URL being localhost-ish."""

    def test_localhost_rewritten(self) -> None:
        client = MagicMock()
        client.browser.get_info.return_value = MagicMock(data=MagicMock(cdp_url="ws://localhost:8080/devtools/browser/abc"))
        assert _cdp_url_for_gateway(client) == "ws://host.docker.internal:8080/devtools/browser/abc"

    def test_ipv4_loopback_rewritten(self) -> None:
        client = MagicMock()
        client.browser.get_info.return_value = MagicMock(data=MagicMock(cdp_url="ws://127.0.0.1:8080/x"))
        out = _cdp_url_for_gateway(client)
        assert out is not None and "host.docker.internal" in out

    def test_zero_zero_zero_zero_rewritten(self) -> None:
        client = MagicMock()
        client.browser.get_info.return_value = MagicMock(data=MagicMock(cdp_url="ws://0.0.0.0:8080/x"))
        out = _cdp_url_for_gateway(client)
        assert out is not None and "host.docker.internal" in out

    def test_routable_host_passes_through(self) -> None:
        """A non-loopback chromium host should NOT be rewritten — works for remote AIO."""
        client = MagicMock()
        client.browser.get_info.return_value = MagicMock(data=MagicMock(cdp_url="ws://chromium.prod.example.com:9222/x"))
        assert _cdp_url_for_gateway(client) == "ws://chromium.prod.example.com:9222/x"

    def test_no_cdp_url_returns_none(self) -> None:
        client = MagicMock()
        client.browser.get_info.return_value = MagicMock(data=MagicMock(cdp_url=None))
        assert _cdp_url_for_gateway(client) is None

    def test_empty_cdp_url_returns_none(self) -> None:
        client = MagicMock()
        client.browser.get_info.return_value = MagicMock(data=MagicMock(cdp_url=""))
        assert _cdp_url_for_gateway(client) is None

    def test_get_info_raises_returns_none(self) -> None:
        client = MagicMock()
        client.browser.get_info.side_effect = RuntimeError("boom")
        assert _cdp_url_for_gateway(client) is None


# ============================================================
# _clamp_render_budget_ms
# ============================================================


class TestClampRenderBudgetMs:
    """Bounds the render budget to [_DEFAULT, _MAX]."""

    def test_none_returns_default(self) -> None:
        assert _clamp_render_budget_ms(None) == _DEFAULT_RENDER_BUDGET_MS

    @pytest.mark.parametrize("bad", [0, -1, -1000])
    def test_non_positive_returns_default(self, bad: int) -> None:
        assert _clamp_render_budget_ms(bad) == _DEFAULT_RENDER_BUDGET_MS

    def test_within_range_passes_through(self) -> None:
        assert _clamp_render_budget_ms(500) == 500
        assert _clamp_render_budget_ms(2500) == 2500

    def test_at_cap_passes(self) -> None:
        assert _clamp_render_budget_ms(_MAX_RENDER_BUDGET_MS) == _MAX_RENDER_BUDGET_MS

    @pytest.mark.parametrize("over", [_MAX_RENDER_BUDGET_MS + 1, _MAX_RENDER_BUDGET_MS * 10, 1_000_000])
    def test_above_cap_clamped(self, over: int) -> None:
        assert _clamp_render_budget_ms(over) == _MAX_RENDER_BUDGET_MS


# ============================================================
# _adaptive_wait
# ============================================================


class TestAdaptiveWait:
    """networkidle fast path + budget-fallback slow path."""

    def test_networkidle_fast_path(self) -> None:
        page = MagicMock()
        page.wait_for_load_state.return_value = None
        elapsed, mode = _adaptive_wait(page, budget_ms=1500)
        assert mode == "networkidle"
        assert elapsed < 500  # should be near-instant for a MagicMock
        page.wait_for_timeout.assert_not_called()

    def test_networkidle_timeout_falls_back_to_budget(self) -> None:
        page = MagicMock()
        page.wait_for_load_state.side_effect = Exception("timeout")
        elapsed, mode = _adaptive_wait(page, budget_ms=100)
        assert mode == "budget_fallback"
        # page.wait_for_timeout called once with remaining budget (≤100ms)
        page.wait_for_timeout.assert_called_once()
        called_ms = page.wait_for_timeout.call_args.args[0]
        assert 0 <= called_ms <= 100

    def test_networkidle_raises_other_error_still_falls_back(self) -> None:
        page = MagicMock()
        page.wait_for_load_state.side_effect = RuntimeError("page closed")
        elapsed, mode = _adaptive_wait(page, budget_ms=200)
        assert mode == "budget_fallback"
        page.wait_for_timeout.assert_called_once()


# ============================================================
# _console_entries_to_errors
# ============================================================


class TestConsoleEntriesToErrors:
    """Extract error/warning lines from get_console() payloads."""

    def test_dict_with_type_error(self) -> None:
        entries = [{"type": "error", "text": "boom"}]
        assert _console_entries_to_errors(entries) == ["boom"]

    def test_dict_with_level_warning(self) -> None:
        entries = [{"level": "warning", "message": "careful"}]
        assert _console_entries_to_errors(entries) == ["careful"]

    def test_dict_with_info_excluded(self) -> None:
        entries = [{"type": "info", "text": "hello"}]
        assert _console_entries_to_errors(entries) == []

    def test_object_with_attrs(self) -> None:
        entry = MagicMock()
        entry.type = "error"
        entry.text = "boom"
        entry.level = None
        entry.message = None
        # The fallback getattr chain reads .text first; mock spec attrs
        result = _console_entries_to_errors([entry])
        assert result == ["boom"]

    def test_data_attribute_wrapped(self) -> None:
        # Some SDKs return an object whose .data is the list.
        wrapper = MagicMock()
        wrapper.data = [{"type": "error", "text": "from-data"}]
        assert _console_entries_to_errors(wrapper) == ["from-data"]

    def test_none_input_returns_empty(self) -> None:
        assert _console_entries_to_errors(None) == []

    def test_truncates_long_messages(self) -> None:
        long = "x" * 500
        entries = [{"type": "error", "text": long}]
        result = _console_entries_to_errors(entries)
        assert len(result) == 1
        assert len(result[0]) == 300


# ============================================================
# RouteResult.to_dict / BrowserCheck.summary
# ============================================================


class TestDataclasses:
    def test_route_result_to_dict_no_screenshot(self) -> None:
        rr = RouteResult(route="/", ok=True, status="ok")
        d = rr.to_dict(include_screenshot=False)
        assert d == {"route": "/", "ok": True, "status": "ok", "console_errors": [], "notes": ""}
        assert "screenshot" not in d

    def test_route_result_to_dict_with_screenshot_b64(self) -> None:
        rr = RouteResult(route="/x", ok=True, status="ok", screenshot_b64="AAAA")
        d = rr.to_dict(include_screenshot=True)
        assert d["screenshot"] == "data:image/png;base64,AAAA"

    def test_route_result_console_errors_truncated_to_20(self) -> None:
        rr = RouteResult(route="/", ok=True, status="ok", console_errors=["e"] * 50)
        d = rr.to_dict()
        assert len(d["console_errors"]) == 20

    def test_browser_check_summary_no_routes(self) -> None:
        bc = BrowserCheck(ok=False, reason="no browser")
        s = bc.summary()
        assert "could not run" in s
        assert "no browser" in s

    def test_browser_check_summary_pass(self) -> None:
        bc = BrowserCheck(
            ok=True,
            reason="ok",
            routes=[RouteResult(route="/", ok=True, status="ok")],
        )
        s = bc.summary()
        assert "✓ PASS" in s
        assert "/" in s
        assert "[ok]" in s

    def test_browser_check_summary_issues(self) -> None:
        bc = BrowserCheck(
            ok=False,
            reason="issues",
            routes=[RouteResult(route="/x", ok=False, status="render_error", notes="Cannot find module")],
        )
        s = bc.summary()
        assert "✗ ISSUES" in s
        assert "Cannot find module" in s

    def test_browser_check_to_dict_includes_routes(self) -> None:
        bc = BrowserCheck(
            ok=True,
            reason="ok",
            port=3000,
            routes=[RouteResult(route="/", ok=True, status="ok")],
        )
        d = bc.to_dict()
        assert d["ok"] is True
        assert d["port"] == 3000
        assert isinstance(d["routes"], list)


# ============================================================
# Locks + deep-copy return semantics (A1)
# ============================================================


class TestPerThreadLock:
    def test_same_thread_id_returns_same_lock(self) -> None:
        assert _get_thread_lock("t1") is _get_thread_lock("t1")

    def test_different_thread_id_returns_different_lock(self) -> None:
        assert _get_thread_lock("t1") is not _get_thread_lock("t2")

    def test_lock_is_a_threading_lock(self) -> None:
        assert isinstance(_get_thread_lock("t-check"), type(_LAST_CHECKS_LOCK))

    def test_concurrent_calls_serialised(self) -> None:
        """Two concurrent run_browser_check calls on the same thread_id must serialise."""
        counter = {"active": 0, "max_active": 0, "completed": 0}
        counter_lock = threading.Lock()
        call_order: list[str] = []

        def fake_unlocked(thread_id, sandbox, *, label, routes, with_screenshot, render_budget_ms):
            with counter_lock:
                counter["active"] += 1
                counter["max_active"] = max(counter["max_active"], counter["active"])
            call_order.append(f"start-{thread_id}-{counter['completed']}")
            time.sleep(0.05)
            with counter_lock:
                counter["active"] -= 1
                counter["completed"] += 1
            call_order.append(f"end-{thread_id}")
            return BrowserCheck(ok=True, reason="ok")

        sandbox = MagicMock()
        sandbox._client = None  # triggers early-return path; we patch unlock below

        with patch.object(bc, "_run_browser_check_unlocked", side_effect=fake_unlocked):
            threads = [threading.Thread(target=run_browser_check, args=("t-conc", sandbox)) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # Lock serialises calls; active must never exceed 1.
        assert counter["max_active"] == 1, f"expected serialised (max_active=1), got {counter['max_active']}"
        assert counter["completed"] == 4
        # Interleaved ordering: every start must be followed by its own end
        # before the NEXT start appears. The labels carry the index, so
        # we can pair them precisely (avoid list.index which returns first match).
        starts = [e for e in call_order if e.startswith("start-")]
        ends = [e for e in call_order if e.startswith("end-")]
        assert len(starts) == len(ends) == 4
        # For each start_i, find the nearest end_i that comes AFTER it.
        # We just verify the schedule is non-overlapping: starts[i+1]
        # appears AFTER ends[i] in the sequence.
        for i in range(len(starts) - 1):
            start_idx = call_order.index(starts[i + 1])
            end_idx = call_order.index(ends[i])
            assert end_idx < start_idx, f"start[{i + 1}] at {start_idx} appeared before end[{i}] at {end_idx}; sequence: {call_order}"


class TestDeepCopyReturn:
    def test_run_browser_check_returns_deepcopy(self) -> None:
        """The returned BrowserCheck must NOT share state with the cache."""

        def fake_unlocked(thread_id, sandbox, *, label, routes, with_screenshot, render_budget_ms):
            return BrowserCheck(
                ok=True,
                reason="ok",
                routes=[RouteResult(route="/", ok=True, status="ok")],
            )

        sandbox = MagicMock()
        with patch.object(bc, "_run_browser_check_unlocked", side_effect=fake_unlocked):
            first = run_browser_check("t-deep", sandbox)
            # mutate the returned object
            first.reason = "mutated"
            first.routes[0].route = "/mutated"
            # subsequent call (still patched) returns fresh result
            second = run_browser_check("t-deep", sandbox)
        # cached result must not be affected
        assert second.reason == "ok"
        assert second.routes[0].route == "/"

    def test_get_last_returns_deepcopy(self) -> None:
        def fake_unlocked(thread_id, sandbox, *, label, routes, with_screenshot, render_budget_ms):
            return BrowserCheck(
                ok=True,
                reason="ok",
                routes=[RouteResult(route="/", ok=True, status="ok")],
            )

        sandbox = MagicMock()
        with patch.object(bc, "_run_browser_check_unlocked", side_effect=fake_unlocked):
            run_browser_check("t-cache", sandbox)

        cached = get_last_browser_check("t-cache")
        assert cached is not None
        cached.reason = "mutated"
        cached.routes[0].route = "/mutated"

        cached2 = get_last_browser_check("t-cache")
        assert cached2.reason == "ok"
        assert cached2.routes[0].route == "/"

    def test_get_last_returns_none_for_unknown_thread(self) -> None:
        assert get_last_browser_check("never-seen-thread") is None


# ============================================================
# Unlocked inner: no client → "no browser" reason
# ============================================================


class TestUnlockedNoClient:
    def test_no_client_returns_reason(self) -> None:
        sandbox = MagicMock(spec=[])  # no _client attribute
        result = _run_browser_check_unlocked(
            "t-noclient",
            sandbox,
            label="app",
            routes=None,
            with_screenshot=True,
            render_budget_ms=1500,
        )
        assert result.ok is False
        assert "no browser" in result.reason.lower()

    def test_no_client_and_no_dev_server_no_html(self) -> None:
        # Sandbox has _client (so we get past the first check) but no dev server,
        # and execute_command returns nothing for the static-html lookup.
        sandbox = MagicMock()
        sandbox._client = MagicMock()  # non-None, but unused since no dev server

        from deerflow.sandbox import dev_server as ds_mod

        # Stub get_dev_server to return None (no dev server running).
        with patch.object(ds_mod, "get_dev_server", return_value=None):
            # execute_command returns empty (no static html deliverable)
            sandbox.execute_command.return_value = ""
            result = _run_browser_check_unlocked(
                "t-noserv",
                sandbox,
                label="app",
                routes=None,
                with_screenshot=False,
                render_budget_ms=1500,
            )
        assert result.ok is False
        assert "no html deliverable" in result.reason or "no dev server" in result.reason
