"""DiagnosticsServiceImpl against the real diagnostics recorder.

The service used to import a ``Diagnostics`` class that never existed in
``deerflow.runtime.stream_bridge.diagnostics`` (the module exports the
``diagnostics`` instance of ``_Diagnostics``). The EventBus swallows handler
exceptions, so every run event the service was subscribed to raised
ImportError and was silently dropped. These tests drive the real recorder
and the real EventBus end to end, with no mocks of the diagnostics module.
"""

from __future__ import annotations

from collections import deque

import pytest

from deerflow.events.bus import EventBus
from deerflow.events.event import RunCompleted, RunStarted
from deerflow.runtime.stream_bridge import diagnostics as diagnostics_module
from deerflow.services.implementations import DiagnosticsServiceImpl
from deerflow.services.types import DiagnosticsRecord


@pytest.fixture
def enabled_recorder(monkeypatch):
    """Switch the process recorder on with an in-memory sink."""
    recorder = diagnostics_module.diagnostics
    lines: list[str] = []
    monkeypatch.setattr(recorder, "_enabled", True)
    monkeypatch.setattr(recorder, "_sink", lines.append)
    monkeypatch.setattr(recorder, "_ring", deque(maxlen=1000))
    return lines


def test_record_and_read_recent_round_trip(enabled_recorder):
    svc = DiagnosticsServiceImpl()
    svc.record("probe", run_id="r1", thread_id="t1", correlation_id="c1", extra={"k": 1})

    records = svc.read_recent(10)

    assert len(enabled_recorder) == 1
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, DiagnosticsRecord)
    assert rec.stage == "probe"
    assert rec.run_id == "r1"
    assert rec.thread_id == "t1"
    assert rec.correlation_id == "c1"
    assert rec.extra == {"k": 1}
    assert rec.seq > 0
    assert rec.wall_iso


def test_read_recent_honours_limit(enabled_recorder):
    svc = DiagnosticsServiceImpl()
    for i in range(5):
        svc.record(f"s{i}")
    records = svc.read_recent(2)
    assert [r.stage for r in records] == ["s3", "s4"]
    # Empty ids are not stamped onto the record as "".
    assert records[0].run_id == ""


def test_record_is_noop_when_recorder_disabled(monkeypatch):
    recorder = diagnostics_module.diagnostics
    monkeypatch.setattr(recorder, "_enabled", False)
    svc = DiagnosticsServiceImpl()
    svc.record("probe", run_id="r1")
    assert svc.read_recent(10) == []


def test_subscribe_to_events_records_run_events(enabled_recorder):
    bus = EventBus()
    svc = DiagnosticsServiceImpl()
    svc.subscribe_to_events(bus)

    started = RunStarted(run_id="run-a", thread_id="thread-a", correlation_id="corr-a", payload={"model": "m"})
    bus.publish(started)
    bus.publish(RunCompleted(run_id="run-a", thread_id="thread-a", correlation_id="corr-a"))

    records = svc.read_recent(10)
    assert [r.stage for r in records] == ["event:RunStarted", "event:RunCompleted"]
    first = records[0]
    assert first.run_id == "run-a"
    assert first.thread_id == "thread-a"
    assert first.correlation_id == "corr-a"
    assert first.extra["event_id"] == started.event_id
    assert first.extra["model"] == "m"
