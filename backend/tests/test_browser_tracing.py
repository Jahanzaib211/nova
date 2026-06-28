"""Unit tests for the browser tracing helpers (v7 B1 + B6).

Covers trace_id propagation across threads, span lifecycle, parent/child
linking, status on exception, and the structured-log emission contract.
"""

from __future__ import annotations

import io
import json
import logging
import threading

import pytest

from deerflow.sandbox import browser_tracing as bt
from deerflow.sandbox.browser_tracing import (
    browser_log,
    browser_span,
    current_span,
    get_trace_id,
    new_trace_id,
    set_trace_id,
)


@pytest.fixture(autouse=True)
def _reset_trace():
    """Reset trace + span stack before AND after each test for isolation.

    The ContextVar reset inside browser_span's finally block is correct,
    but pytest's per-test task scheduling means we explicitly clear state
    at fixture teardown so subsequent tests start from a known empty stack.
    """
    set_trace_id(None)
    bt._current_spans.set(None)
    yield
    set_trace_id(None)
    bt._current_spans.set(None)


class TestTraceId:
    def test_new_trace_id_format(self) -> None:
        tid = new_trace_id()
        assert isinstance(tid, str)
        assert len(tid) == 32  # 16 random bytes hex
        # Distinct across calls
        assert new_trace_id() != tid

    def test_set_get_clear(self) -> None:
        assert get_trace_id() is None
        set_trace_id("trace-abc")
        assert get_trace_id() == "trace-abc"
        set_trace_id(None)
        assert get_trace_id() is None

    def test_thread_local_propagation(self) -> None:
        """set_trace_id in one thread doesn't leak to another."""
        set_trace_id("trace-A")

        def worker():
            assert get_trace_id() is None
            set_trace_id("trace-B")
            assert get_trace_id() == "trace-B"

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        # Main thread's trace unchanged
        assert get_trace_id() == "trace-A"


class TestSpanLifecycle:
    def test_root_span_has_no_parent(self) -> None:
        with browser_span("outer") as s:
            assert s.parent_span_id is None
            assert s.trace_id is not None
            assert s.span_id is not None

    def test_span_status_ok_on_clean_exit(self) -> None:
        with browser_span("ok") as s:
            pass
        assert s.status == "ok"
        assert s.end_time is not None

    def test_span_status_error_on_exception(self) -> None:
        with pytest.raises(ValueError):
            with browser_span("fail") as s:
                raise ValueError("boom")
        assert s.status == "error"
        assert any(e["name"] == "exception" for e in s.events)

    def test_span_attributes_preserved(self) -> None:
        with browser_span("with_attrs", attributes={"route": "/x", "thread_id": "t1"}) as s:
            assert s.attributes["route"] == "/x"
            assert s.attributes["thread_id"] == "t1"

    def test_span_set_attribute_after_creation(self) -> None:
        with browser_span("s") as s:
            s.set_attribute("k", "v")
        assert s.attributes["k"] == "v"

    def test_span_to_dict_shape(self) -> None:
        with browser_span("d") as s:
            s.set_attribute("a", 1)
            s.add_event("checkpoint", x=2)
            s.set_status("ok")
        d = s.to_dict()
        assert d["name"] == "d"
        assert d["status"] == "ok"
        assert d["attributes"]["a"] == 1
        assert any(e["name"] == "checkpoint" for e in d["events"])
        assert "duration_ms" in d
        assert d["duration_ms"] >= 0


class TestSpanParentLinking:
    def test_child_links_to_parent(self) -> None:
        with browser_span("parent") as p:
            p_id = p.span_id
            with browser_span("child") as c:
                assert c.parent_span_id == p_id, f"c.parent_span_id={c.parent_span_id}, p.span_id={p_id}"
                assert c.trace_id == p.trace_id
                assert current_span() is c
            assert current_span() is p

    def test_grandchild_chain(self) -> None:
        with browser_span("a") as a:
            with browser_span("b") as b:
                with browser_span("c") as c:
                    assert c.parent_span_id == b.span_id
                    assert b.parent_span_id == a.span_id

    def test_siblings_have_distinct_span_ids(self) -> None:
        with browser_span("root") as root:
            with browser_span("s1") as s1:
                pass
            with browser_span("s2") as s2:
                pass
        assert s1.span_id != s2.span_id
        assert s1.parent_span_id == root.span_id
        assert s2.parent_span_id == root.span_id


class TestCurrentSpan:
    def test_no_active_span(self) -> None:
        assert current_span() is None

    def test_active_span_reflected(self) -> None:
        with browser_span("now") as s:
            assert current_span() is s


class TestBrowserLog:
    @pytest.fixture
    def captured_logs(self):
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("deerflow.sandbox.browser_tracing")
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        yield buf
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    def test_event_field_present(self, captured_logs) -> None:
        browser_log("test.event")
        line = json.loads(captured_logs.getvalue().strip())
        assert line["event"] == "test.event"

    def test_extra_fields_passed_through(self, captured_logs) -> None:
        browser_log("test.x", thread_id="t1", route="/y")
        line = json.loads(captured_logs.getvalue().strip())
        assert line["thread_id"] == "t1"
        assert line["route"] == "/y"

    def test_trace_id_included_when_set(self, captured_logs) -> None:
        set_trace_id("trace-123")
        browser_log("test.t")
        line = json.loads(captured_logs.getvalue().strip())
        assert line["trace_id"] == "trace-123"

    def test_span_id_included_when_inside_span(self, captured_logs) -> None:
        with browser_span("outer"):
            browser_log("test.inside")
        lines = [json.loads(l) for l in captured_logs.getvalue().strip().split("\n") if l]
        inside = [l for l in lines if l["event"] == "test.inside"]
        assert inside[0]["span_id"]

    def test_parent_span_id_for_nested(self, captured_logs) -> None:
        with browser_span("outer") as outer:
            with browser_span("inner"):
                browser_log("test.nested")
        lines = [json.loads(l) for l in captured_logs.getvalue().strip().split("\n") if l]
        nested = [l for l in lines if l["event"] == "test.nested"]
        assert nested[0]["parent_span_id"] == outer.span_id

    def test_non_serializable_falls_back_to_repr(self, captured_logs) -> None:
        class Weird:
            def __repr__(self):
                return "<weird-marker>"

        browser_log("test.weird", x=Weird())
        # Even if JSON failed, the event name appears in output.
        content = captured_logs.getvalue()
        assert "test.weird" in content

    def test_span_attributes_merged(self, captured_logs) -> None:
        with browser_span("merged", attributes={"k": "v"}):
            browser_log("test.merged")
        line = json.loads([l for l in captured_logs.getvalue().strip().split("\n") if "test.merged" in l][0])
        assert line["span.k"] == "v"
