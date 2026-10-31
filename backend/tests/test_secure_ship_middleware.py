"""Tests for SecureShipMiddleware - the gate in front of every delivery.

The gate engine is mocked: what matters here is the middleware's contract --
a ``block`` verdict stops the delivery, a ``warn``/``pass`` verdict lets it
through with a note, and a gate that cannot run never stops the delivery.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage

from deerflow.agents.middlewares import secure_ship_middleware
from deerflow.agents.middlewares.secure_ship_middleware import SecureShipMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeFinding:
    def __init__(self, severity: str, title: str, path: str | None = None) -> None:
        self.severity = severity
        self.title = title
        self.path = path


class _FakeReport:
    """The engine's report, reduced to what the middleware reads."""

    def __init__(self, verdict: str = "pass", counts: dict | None = None, findings: list | None = None, reason: str | None = None) -> None:
        self.thread_id = "thread-1"
        self.verdict = verdict
        self.counts = counts if counts is not None else {}
        self.findings = findings or []
        self.reason = reason
        self.duration_ms = 11

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "verdict": self.verdict,
            "counts": self.counts,
            "findings": self.findings,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
        }


def _gate(report, calls: list | None = None):
    """A stand-in for ``run_gate`` returning *report*."""

    async def run_gate(thread_id, *, user_id=None, policy=None, deep=False):
        if calls is not None:
            calls.append({"thread_id": thread_id, "user_id": user_id, "deep": deep})
        return report

    return run_gate


def _failing_gate(message: str = "scanner offline"):
    async def run_gate(thread_id, *, user_id=None, policy=None, deep=False):
        raise RuntimeError(message)

    return run_gate


def _make_request(tool_name: str = "deploy_expose", thread_id: str | None = "thread-1") -> MagicMock:
    request = MagicMock()
    request.tool_call = {"name": tool_name, "id": "call-1", "args": {}}
    request.runtime = SimpleNamespace(
        context={"thread_id": thread_id} if thread_id else {},
        config={"configurable": {"thread_id": thread_id}} if thread_id else {},
        state={},
    )
    return request


def _make_handler(name: str = "deploy_expose"):
    return MagicMock(return_value=ToolMessage(content="deployed", tool_call_id="call-1", name=name))


# ---------------------------------------------------------------------------
# Block
# ---------------------------------------------------------------------------


class TestBlockVerdict:
    def test_block_stops_the_handler(self, monkeypatch):
        report = _FakeReport(verdict="block", counts={"high": 1}, findings=[_FakeFinding("high", "Possible hard-coded secret", "app/config.py")])
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        handler = _make_handler()
        result = mw.wrap_tool_call(_make_request(), handler)

        assert not handler.called
        assert isinstance(result, ToolMessage)
        assert result.status == "error"
        assert result.tool_call_id == "call-1"
        assert result.name == "deploy_expose"

    def test_block_message_names_what_was_found(self, monkeypatch):
        report = _FakeReport(verdict="block", counts={"high": 1}, findings=[_FakeFinding("high", "Possible hard-coded secret", "app/config.py")])
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        result = mw.wrap_tool_call(_make_request(), _make_handler())

        assert "Secure-Ship" in result.content
        assert "1 high finding" in result.content
        assert "Possible hard-coded secret" in result.content
        assert "app/config.py" in result.content
        assert "Fix it and retry" in result.content

    def test_present_files_is_gated_too(self, monkeypatch):
        report = _FakeReport(verdict="block", counts={"high": 1}, findings=[_FakeFinding("high", "Possible hard-coded secret", "app/config.py")])
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        handler = _make_handler(name="present_files")
        result = mw.wrap_tool_call(_make_request("present_files"), handler)

        assert not handler.called
        assert result.status == "error"
        assert result.name == "present_files"


# ---------------------------------------------------------------------------
# Warn / pass
# ---------------------------------------------------------------------------


class TestAllowVerdicts:
    def test_pass_calls_the_handler_and_appends_a_note(self, monkeypatch):
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(_FakeReport(verdict="pass", counts={})))

        mw = SecureShipMiddleware()
        handler = _make_handler()
        result = mw.wrap_tool_call(_make_request(), handler)

        assert handler.called
        assert isinstance(result, ToolMessage)
        assert "Secure-Ship" in result.content
        assert "gate passed" in result.content
        assert "0 findings" in result.content

    def test_warn_calls_the_handler_and_appends_a_note(self, monkeypatch):
        report = _FakeReport(verdict="warn", counts={"medium": 1}, findings=[_FakeFinding("medium", "Weak hash", "app/auth.py")])
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        handler = _make_handler()
        result = mw.wrap_tool_call(_make_request(), handler)

        assert handler.called
        assert "Secure-Ship: 1 medium finding, review before shipping." in result.content

    def test_the_note_keeps_the_original_tool_output(self, monkeypatch):
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(_FakeReport(verdict="pass")))

        mw = SecureShipMiddleware()
        result = mw.wrap_tool_call(_make_request(), _make_handler())
        assert "deployed" in result.content


# ---------------------------------------------------------------------------
# Fail open
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_a_failing_gate_does_not_block(self, monkeypatch):
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _failing_gate())

        mw = SecureShipMiddleware()
        handler = _make_handler()
        result = mw.wrap_tool_call(_make_request(), handler)

        assert handler.called
        assert isinstance(result, ToolMessage)
        assert result.status != "error"
        assert "Secure-Ship could not run" in result.content
        assert "scanner offline" in result.content

    def test_a_missing_thread_does_not_block(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(_FakeReport(verdict="block"), calls))

        mw = SecureShipMiddleware()
        handler = _make_handler()
        result = mw.wrap_tool_call(_make_request(thread_id=None), handler)

        assert handler.called
        assert calls == []
        assert "Secure-Ship could not run" in result.content

    def test_an_error_verdict_does_not_block(self, monkeypatch):
        """The gate ran but could not decide: that is a failure to scan."""
        report = _FakeReport(verdict="error", reason="scanner timed out")
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        handler = _make_handler()
        result = mw.wrap_tool_call(_make_request(), handler)

        assert handler.called
        assert "Secure-Ship could not run" in result.content
        assert "scanner timed out" in result.content


# ---------------------------------------------------------------------------
# Non-delivery tools
# ---------------------------------------------------------------------------


class TestNonDeliveryTools:
    @pytest.mark.parametrize("tool_name", ["write_file", "bash", "read_file", "str_replace", "start_dev_server"])
    def test_other_tools_are_never_intercepted(self, monkeypatch, tool_name):
        calls: list = []
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(_FakeReport(verdict="block"), calls))

        mw = SecureShipMiddleware()
        handler = _make_handler(name=tool_name)
        result = mw.wrap_tool_call(_make_request(tool_name), handler)

        assert handler.called
        assert result is handler.return_value
        assert calls == [], "the gate must not run for a non-delivery tool"

    def test_a_non_delivery_tool_is_not_annotated(self, monkeypatch):
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(_FakeReport(verdict="pass")))

        mw = SecureShipMiddleware()
        handler = _make_handler(name="write_file")
        result = mw.wrap_tool_call(_make_request("write_file"), handler)

        assert result is handler.return_value
        assert "Secure-Ship" not in result.content


# ---------------------------------------------------------------------------
# The custom event
# ---------------------------------------------------------------------------


class TestSecureShipEvent:
    def test_the_event_is_emitted_with_the_verdict(self, monkeypatch):
        events: list[dict] = []
        monkeypatch.setattr("langgraph.config.get_stream_writer", lambda: events.append)
        report = _FakeReport(verdict="warn", counts={"medium": 1})
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        mw.wrap_tool_call(_make_request(), _make_handler())

        assert len(events) == 1
        event = events[0]
        assert event["type"] == "secure_ship"
        assert event["thread_id"] == "thread-1"
        assert event["verdict"] == "warn"
        assert event["counts"] == {"medium": 1}
        assert event["duration_ms"] == 11

    def test_no_writer_is_not_an_error(self, monkeypatch):
        def boom():
            raise RuntimeError("no stream writer")

        monkeypatch.setattr("langgraph.config.get_stream_writer", boom)
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(_FakeReport(verdict="pass")))

        mw = SecureShipMiddleware()
        handler = _make_handler()
        result = mw.wrap_tool_call(_make_request(), handler)

        assert handler.called
        assert isinstance(result, ToolMessage)


# ---------------------------------------------------------------------------
# Report shapes
# ---------------------------------------------------------------------------


class TestReportShapes:
    """The engine's report is read defensively: counts may be a mapping, an
    object, or absent entirely."""

    def test_a_total_key_is_not_read_as_a_severity(self, monkeypatch):
        report = SimpleNamespace(
            verdict="block",
            counts={"high": 1, "total": 3},
            findings=[_FakeFinding("high", "Possible hard-coded secret", "app/config.py")],
        )
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        result = mw.wrap_tool_call(_make_request(), _make_handler())

        assert "1 high finding" in result.content
        assert "3 high" not in result.content

    def test_counts_as_an_object_are_read(self, monkeypatch):
        report = SimpleNamespace(
            verdict="warn",
            counts=SimpleNamespace(high=0, medium=2),
            findings=[_FakeFinding("medium", "Weak hash", "app/auth.py")],
        )
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        result = mw.wrap_tool_call(_make_request(), _make_handler())

        assert "2 medium findings" in result.content

    def test_a_report_without_counts_is_tallied_from_its_findings(self, monkeypatch):
        report = SimpleNamespace(verdict="warn", findings=[_FakeFinding("low", "Verbose error", "app/main.py")])
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        result = mw.wrap_tool_call(_make_request(), _make_handler())

        assert "1 low finding" in result.content

    def test_severities_are_ordered_worst_first(self, monkeypatch):
        report = _FakeReport(
            verdict="block",
            counts={"low": 1, "high": 1, "medium": 1},
            findings=[
                _FakeFinding("low", "Verbose error", "app/main.py"),
                _FakeFinding("high", "Possible hard-coded secret", "app/config.py"),
                _FakeFinding("medium", "Weak hash", "app/auth.py"),
            ],
        )
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        result = mw.wrap_tool_call(_make_request(), _make_handler())

        assert "1 high and 1 medium and 1 low findings" in result.content
        assert "Possible hard-coded secret" in result.content


# ---------------------------------------------------------------------------
# The async path
# ---------------------------------------------------------------------------


class TestAsyncPath:
    @pytest.mark.asyncio
    async def test_block_stops_the_handler(self, monkeypatch):
        report = _FakeReport(verdict="block", counts={"high": 1}, findings=[_FakeFinding("high", "Possible hard-coded secret", "app/config.py")])
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(report))

        mw = SecureShipMiddleware()
        handler = _make_handler()

        async def async_handler(request):
            return handler(request)

        result = await mw.awrap_tool_call(_make_request(), async_handler)

        assert not handler.called
        assert isinstance(result, ToolMessage)
        assert result.status == "error"
        assert "Secure-Ship" in result.content
        assert "Possible hard-coded secret" in result.content

    @pytest.mark.asyncio
    async def test_pass_calls_the_handler_and_appends_a_note(self, monkeypatch):
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(_FakeReport(verdict="pass")))

        mw = SecureShipMiddleware()
        handler = _make_handler()

        async def async_handler(request):
            return handler(request)

        result = await mw.awrap_tool_call(_make_request(), async_handler)

        assert handler.called
        assert "gate passed" in result.content

    @pytest.mark.asyncio
    async def test_a_failing_gate_does_not_block(self, monkeypatch):
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _failing_gate())

        mw = SecureShipMiddleware()
        handler = _make_handler()

        async def async_handler(request):
            return handler(request)

        result = await mw.awrap_tool_call(_make_request(), async_handler)

        assert handler.called
        assert "Secure-Ship could not run" in result.content

    @pytest.mark.asyncio
    async def test_other_tools_are_never_intercepted(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(secure_ship_middleware, "run_gate", _gate(_FakeReport(verdict="block"), calls))

        mw = SecureShipMiddleware()
        handler = _make_handler(name="write_file")

        async def async_handler(request):
            return handler(request)

        result = await mw.awrap_tool_call(_make_request("write_file"), async_handler)

        assert handler.called
        assert result is handler.return_value
        assert calls == []
