"""Behavioural tests for the browser page-op helpers in workspace_tools.

Covers the four hardening fixes:

* ``_should_fallback_to_cdp`` — SDK signature drift must degrade to CDP
  instead of hard-failing (the bug that made ``browser_eval`` unreachable).
* ``_active_page`` — pick the genuinely visible tab, not ``pages[0]``.
* ``_eval_js`` / ``_format_eval_result`` — multi-statement scripts and
  JSON-shaped results.
* ``_selector_action`` — iframe piercing and actionable failure messages.

These use hand-rolled fakes rather than a real browser: the point is the
decision logic around Playwright, which is what actually regressed.
"""

from __future__ import annotations

import json

import pytest

from deerflow.tools.builtins.workspace_tools import (
    _active_page,
    _browser_op_timeout_ms,
    _eval_js,
    _format_eval_result,
    _selector_action,
    _should_fallback_to_cdp,
)


class _ApiError(Exception):
    """Mirrors agent_sandbox.core.api_error.ApiError's shape."""

    def __init__(self, status_code: int, body: str = "") -> None:
        super().__init__(f"status_code: {status_code}, body: {body}")
        self.status_code = status_code
        self.body = body


class TestShouldFallbackToCdp:
    def test_real_404_falls_back(self) -> None:
        """Image predates /v1/browser_page/* — CDP is the correct path."""
        assert _should_fallback_to_cdp(_ApiError(404, "Not Found")) is True

    def test_non_404_status_does_not_fall_back(self) -> None:
        """A 500 is a real server error; silently re-running a side-effectful
        op over CDP would be wrong."""
        assert _should_fallback_to_cdp(_ApiError(500, "Internal Server Error")) is False

    def test_status_code_wins_over_message_substring(self) -> None:
        """The regression the old substring gate had: a page-level error whose
        *body* mentions 404 must not be mistaken for a missing REST route."""
        exc = _ApiError(500, "upstream returned 404 Not Found for the requested page")
        assert _should_fallback_to_cdp(exc) is False

    @pytest.mark.parametrize(
        "exc",
        [
            TypeError("evaluate() got an unexpected keyword argument 'script'"),
            TypeError("fill() missing 1 required keyword-only argument: 'text'"),
            AttributeError("'BrowserPageClient' object has no attribute 'evaluate'"),
        ],
    )
    def test_signature_drift_falls_back(self, exc: Exception) -> None:
        """The core fix. These are raised *before* any HTTP request; the old
        gate re-raised them, making the CDP fallback below unreachable and
        leaving browser_eval/browser_input permanently broken."""
        assert _should_fallback_to_cdp(exc) is True

    def test_legacy_stringified_404_still_falls_back(self) -> None:
        assert _should_fallback_to_cdp(Exception("HTTP 404: Not Found")) is True

    def test_unrelated_error_propagates(self) -> None:
        assert _should_fallback_to_cdp(ValueError("selector did not match")) is False


class _FakePage:
    def __init__(self, visibility: str = "visible", *, url: str = "http://x", title: str = "T", raises: bool = False) -> None:
        self._visibility = visibility
        self.url = url
        self._title = title
        self._raises = raises
        self.closed = False

    def evaluate(self, expr):  # noqa: ANN001
        if self._raises:
            raise RuntimeError("target crashed")
        if expr == "document.visibilityState":
            return self._visibility
        return expr

    def title(self):
        return self._title

    def close(self):
        self.closed = True


class _FakeCtx:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages
        self.created: list[_FakePage] = []

    def new_page(self):
        page = _FakePage()
        self.created.append(page)
        self.pages.append(page)
        return page


class TestActivePage:
    def test_picks_the_visible_tab_not_the_first(self) -> None:
        """The bug: ctx.pages[0] is the first tab, which is the visible one
        only by coincidence. With a docs tab open alongside the app under
        test, every click landed on the wrong page."""
        hidden = _FakePage("hidden", title="old tab")
        visible = _FakePage("visible", title="the real one")
        page, created = _active_page(_FakeCtx([hidden, visible]))
        assert page is visible
        assert created is False

    def test_falls_back_to_most_recent_when_none_report_visible(self) -> None:
        first = _FakePage("hidden")
        last = _FakePage("hidden")
        page, created = _active_page(_FakeCtx([first, last]))
        assert page is last
        assert created is False

    def test_crashed_tab_does_not_break_selection(self) -> None:
        """A tab whose evaluate() raises must degrade the choice, not the run."""
        crashed = _FakePage(raises=True)
        good = _FakePage("visible")
        page, _ = _active_page(_FakeCtx([crashed, good]))
        assert page is good

    def test_creates_a_page_when_context_is_empty(self) -> None:
        ctx = _FakeCtx([])
        page, created = _active_page(ctx)
        assert created is True
        assert page in ctx.created

    def test_borrowed_page_is_flagged_as_not_created(self) -> None:
        """screenshot() only closes the page when created is True — closing a
        borrowed tab would destroy the user's live session."""
        _, created = _active_page(_FakeCtx([_FakePage("visible")]))
        assert created is False


class TestFormatEvalResult:
    def test_dict_becomes_json_not_python_repr(self) -> None:
        """str() gave {'a': 1} — single quotes, unparseable as JSON."""
        out = _format_eval_result({"a": 1, "b": None, "c": True})
        assert json.loads(out) == {"a": 1, "b": None, "c": True}

    def test_list_becomes_json(self) -> None:
        assert json.loads(_format_eval_result([1, "two", None])) == [1, "two", None]

    def test_none_renders_empty_so_caller_reports_no_result(self) -> None:
        assert _format_eval_result(None) == ""

    def test_string_passes_through_unquoted(self) -> None:
        assert _format_eval_result("hello") == "hello"

    def test_unserializable_is_stringified_inside_valid_json(self) -> None:
        """`default=str` keeps the output parseable even for values JSON has
        no representation for — the caller gets a JSON string, not a crash."""
        out = _format_eval_result(object())
        assert json.loads(out).startswith("<object")


class _EvalPage:
    """Rejects multi-statement bodies the way a real JS engine does."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def evaluate(self, script):  # noqa: ANN001
        self.calls.append(script)
        if "return " in script and not script.strip().startswith("(()"):
            raise RuntimeError("SyntaxError: Illegal return statement")
        return {"ok": True}


class TestEvalJs:
    def test_expression_evaluates_directly(self) -> None:
        page = _EvalPage()
        assert _eval_js(page, "document.title") == {"ok": True}
        assert page.calls == ["document.title"]

    def test_multi_statement_body_is_wrapped_in_an_iife(self) -> None:
        """`const x = 1; return x;` is the shape a model reaches for first,
        and it is a hard JS SyntaxError as a bare expression."""
        page = _EvalPage()
        result = _eval_js(page, "const x = 1; return x;")
        assert result == {"ok": True}
        assert len(page.calls) == 2
        assert page.calls[1].startswith("(() => {")

    def test_runtime_error_is_not_retried(self) -> None:
        """Only syntax errors get the IIFE retry — a genuine exception must
        surface as itself rather than being masked by a second attempt."""

        class Boom:
            def __init__(self) -> None:
                self.calls = 0

            def evaluate(self, script):  # noqa: ANN001, ARG002
                self.calls += 1
                raise RuntimeError("TypeError: x is not a function")

        page = Boom()
        with pytest.raises(RuntimeError, match="not a function"):
            _eval_js(page, "x()")
        assert page.calls == 1


class _Frame:
    def __init__(self, name: str, *, has: bool) -> None:
        self.name = name
        self._has = has
        self.acted = False

    def wait_for_selector(self, selector, timeout, state):  # noqa: ANN001, ARG002
        if not self._has:
            raise RuntimeError("timeout")
        return object()


class _FramePage:
    def __init__(self, frames: list[_Frame], *, main_has: bool) -> None:
        self.main_frame = _Frame("main", has=main_has)
        self.frames = [self.main_frame, *frames]
        self._main_has = main_has
        self.url = "https://example.test/app"

    def title(self):
        return "App"


class TestSelectorAction:
    def test_main_frame_hit_short_circuits(self) -> None:
        page = _FramePage([], main_has=True)
        seen: list[object] = []

        def action(target, timeout):  # noqa: ANN001, ARG001
            seen.append(target)
            return "done"

        assert _selector_action(page, "#btn", action, what="click") == "done"
        assert seen == [page]

    def test_falls_through_to_the_owning_iframe(self) -> None:
        """Controls inside an iframe are invisible to a main-frame selector —
        previously just a bare timeout with no hint a frame was involved."""
        inner = _Frame("inner", has=True)
        page = _FramePage([_Frame("other", has=False), inner], main_has=False)
        targets: list[object] = []

        def action(target, timeout):  # noqa: ANN001, ARG001
            targets.append(target)
            if target is page:
                raise RuntimeError("no such element")
            return "clicked"

        assert _selector_action(page, "#btn", action, what="click") == "clicked"
        assert targets[-1] is inner

    def test_failure_message_names_the_page_and_frame_count(self) -> None:
        page = _FramePage([_Frame("a", has=False)], main_has=False)

        def action(target, timeout):  # noqa: ANN001, ARG001
            raise RuntimeError("timeout exceeded")

        with pytest.raises(RuntimeError) as excinfo:
            _selector_action(page, "#missing", action, what="click")

        message = str(excinfo.value)
        assert "#missing" in message
        assert "1 child frame" in message
        assert "https://example.test/app" in message


class TestBrowserOpTimeout:
    def test_default_is_thirty_seconds(self) -> None:
        """Raised from 10s: SPA-heavy targets routinely exceed it, which
        looked like a broken selector but was an impatient deadline."""
        assert _browser_op_timeout_ms() == 30000

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEERFLOW_BROWSER_OP_TIMEOUT_MS", "5000")
        assert _browser_op_timeout_ms() == 5000

    def test_garbage_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEERFLOW_BROWSER_OP_TIMEOUT_MS", "soon")
        assert _browser_op_timeout_ms() == 30000

    def test_floor_prevents_an_unusable_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEERFLOW_BROWSER_OP_TIMEOUT_MS", "1")
        assert _browser_op_timeout_ms() == 1000


class _FakeRuntime:
    """Minimal Runtime stand-in: only `state` and `context` are read."""

    def __init__(self, state: dict | None = None) -> None:
        self.state = {} if state is None else state
        self.context: dict = {}
        self.config: dict = {}


class TestAioClientAndThread:
    """Cold-start regression: the browser tools' sandbox resolver.

    ``_aio_client_and_thread`` was the only sandbox consumer that read
    ``runtime.state["sandbox"]["sandbox_id"]`` directly instead of going
    through ``ensure_sandbox_initialized``. On the first tool call of a fresh
    thread that key does not exist yet, so the resolver returned
    "requires a per-thread sandbox" without ever attempting to acquire one —
    the "fails once, works on retry" transient reported from live use. The
    same gap turned a provider-dropped stale container into a burst of
    "sandbox not available" instead of a transparent re-acquire.
    """

    @staticmethod
    def _patch(monkeypatch: pytest.MonkeyPatch, *, sandbox, thread_id="t-1", local=False):
        import deerflow.tools.builtins.workspace_tools as wt

        monkeypatch.setattr(wt, "is_local_sandbox", lambda runtime: local)
        monkeypatch.setattr(wt, "get_thread_data", lambda runtime: {"thread_id": thread_id})
        monkeypatch.setattr(wt, "_extract_thread_id_from_thread_data", lambda data: thread_id)
        calls: list[int] = []

        def fake_ensure(runtime):
            calls.append(1)
            if isinstance(sandbox, Exception):
                raise sandbox
            return sandbox

        monkeypatch.setattr(wt, "ensure_sandbox_initialized", fake_ensure)
        return calls

    def test_empty_state_lazily_acquires_instead_of_erroring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exact cold-start bug: no sandbox in state on the first call."""
        from types import SimpleNamespace

        from deerflow.tools.builtins.workspace_tools import _aio_client_and_thread

        sentinel = object()
        calls = self._patch(monkeypatch, sandbox=SimpleNamespace(id="aio:t-1", _client=sentinel))
        client, thread_id, error = _aio_client_and_thread(_FakeRuntime(state={}))

        assert error is None, f"cold start still fails: {error}"
        assert client is sentinel
        assert thread_id == "t-1"
        assert calls, "must go through ensure_sandbox_initialized, not read state directly"

    def test_released_sandbox_is_reacquired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stale sandbox_id in state — ensure_sandbox_initialized re-acquires."""
        from types import SimpleNamespace

        from deerflow.tools.builtins.workspace_tools import _aio_client_and_thread

        sentinel = object()
        self._patch(monkeypatch, sandbox=SimpleNamespace(id="aio:new", _client=sentinel))
        state = {"sandbox": {"sandbox_id": "aio:dead"}}
        client, _thread_id, error = _aio_client_and_thread(_FakeRuntime(state=state))

        assert error is None
        assert client is sentinel

    def test_local_sandbox_is_still_rejected_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from deerflow.tools.builtins.workspace_tools import _aio_client_and_thread

        self._patch(monkeypatch, sandbox=None, local=True)
        client, thread_id, error = _aio_client_and_thread(_FakeRuntime())
        assert client is None and thread_id is None
        assert "container (AIO) sandbox" in error

    def test_missing_thread_id_is_still_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from deerflow.tools.builtins.workspace_tools import _aio_client_and_thread

        self._patch(monkeypatch, sandbox=None, thread_id=None)
        _client, _thread_id, error = _aio_client_and_thread(_FakeRuntime())
        assert "per-thread sandbox" in error

    def test_acquisition_failure_surfaces_the_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A genuine failure must stay a failure — and say why."""
        from deerflow.tools.builtins.workspace_tools import _aio_client_and_thread

        self._patch(monkeypatch, sandbox=RuntimeError("docker daemon unreachable"))
        _client, _thread_id, error = _aio_client_and_thread(_FakeRuntime())
        assert "sandbox not available" in error
        assert "docker daemon unreachable" in error

    def test_sandbox_without_client_is_reported_distinctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        from deerflow.tools.builtins.workspace_tools import _aio_client_and_thread

        self._patch(monkeypatch, sandbox=SimpleNamespace(id="aio:t-1", _client=None))
        _client, _thread_id, error = _aio_client_and_thread(_FakeRuntime())
        assert "sandbox client unavailable" in error
