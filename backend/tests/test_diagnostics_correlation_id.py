"""Phase C0.5 — backend diagnostics correlation_id propagation.

Verifies that:
- ``register_correlation_id`` stores a mapping that ``record()`` reads
- ``record()`` stamps every emitted payload with the right correlation_id
  when the caller passes run_id / thread_id
- Explicit ``correlation_id`` argument wins over the registry
- The registry is per-process and is NOT cleared on record()
"""

from __future__ import annotations

import io
import json
import logging
import os

import pytest

from deerflow.runtime.stream_bridge import diagnostics
from deerflow.runtime.stream_bridge.diagnostics import (
    _Diagnostics,
    lookup_correlation_id,
    register_correlation_id,
)


@pytest.fixture
def fresh_diagnostics(monkeypatch):
    """Replace the singleton with a captured-sink variant and reset the
    correlation registry so each test starts clean."""
    sink = io.StringIO()
    fake = _Diagnostics()
    fake.__init__()  # reset internal state
    # Replace the env-gated fields with a captured sink so we can assert
    # the JSON output directly.
    fake._enabled = True
    fake._sink = lambda line: sink.write(line + "\n")
    monkeypatch.setattr(diagnostics, "diagnostics", fake)
    # Also reset the correlation registry maps (process-scoped).
    import deerflow.runtime.stream_bridge.diagnostics as mod

    mod._correlation_by_thread.clear()
    mod._correlation_by_run.clear()
    return fake, sink


def _last_record(sink: io.StringIO) -> dict:
    lines = [ln for ln in sink.getvalue().splitlines() if ln.strip()]
    assert lines, "no diagnostic lines were emitted"
    return json.loads(lines[-1])


def test_record_emits_correlation_id_from_registry_by_thread(fresh_diagnostics):
    _, sink = fresh_diagnostics
    register_correlation_id(thread_id="thread-a", correlation_id="corr-thread-a")
    diagnostics.diagnostics.record("worker.publish", run_id="r1", thread_id="thread-a")
    rec = _last_record(sink)
    assert rec["correlation_id"] == "corr-thread-a"


def test_record_emits_correlation_id_from_registry_by_run(fresh_diagnostics):
    _, sink = fresh_diagnostics
    register_correlation_id(run_id="r2", correlation_id="corr-run-r2")
    diagnostics.diagnostics.record("bridge.publish", run_id="r2", thread_id="t-x")
    rec = _last_record(sink)
    assert rec["correlation_id"] == "corr-run-r2"


def test_explicit_correlation_id_argument_wins_over_registry(fresh_diagnostics):
    _, sink = fresh_diagnostics
    register_correlation_id(run_id="r3", correlation_id="from-registry")
    diagnostics.diagnostics.record(
        "bridge.publish",
        run_id="r3",
        thread_id="t-y",
        correlation_id="explicit",
    )
    rec = _last_record(sink)
    assert rec["correlation_id"] == "explicit"


def test_record_without_registry_or_arg_omits_correlation_id(fresh_diagnostics):
    _, sink = fresh_diagnostics
    diagnostics.diagnostics.record("bridge.publish", run_id="r-noop")
    rec = _last_record(sink)
    assert "correlation_id" not in rec


def test_lookup_returns_none_when_unregistered(fresh_diagnostics):
    assert lookup_correlation_id(run_id="nonexistent") is None
    assert lookup_correlation_id(thread_id="nonexistent") is None


def test_register_is_idempotent_for_same_value(fresh_diagnostics):
    _, sink = fresh_diagnostics
    register_correlation_id(thread_id="t", correlation_id="v1")
    register_correlation_id(thread_id="t", correlation_id="v1")
    register_correlation_id(thread_id="t", correlation_id="v2")  # overwrite is allowed
    diagnostics.diagnostics.record("x", thread_id="t")
    rec = _last_record(sink)
    assert rec["correlation_id"] == "v2"


def test_register_ignores_empty_and_none(fresh_diagnostics):
    register_correlation_id(thread_id="t", correlation_id=None)
    register_correlation_id(thread_id="t", correlation_id="")
    assert lookup_correlation_id(thread_id="t") is None


def test_record_thread_id_only_still_resolves_correlation(fresh_diagnostics):
    """The registry is keyed primarily on thread_id; passing only
    thread_id (no run_id) must still resolve the correlation_id.
    """
    _, sink = fresh_diagnostics
    register_correlation_id(thread_id="t-only", correlation_id="corr-only-thread")
    diagnostics.diagnostics.record("bridge.subscribe.yield", thread_id="t-only")
    rec = _last_record(sink)
    assert rec["correlation_id"] == "corr-only-thread"
