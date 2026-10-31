"""Token/count fields must survive the service layer on their way to the wire.

The runs API declares every counter (``total_input_tokens``, ``total_tokens``,
``llm_call_count``, ``lead_agent_tokens``, ...). They exist on ``RunRecord`` and
are persisted by the run store. ``RunSummary`` did not carry them and
``_service_to_response`` did not map them, so ``GET /api/threads/{id}/runs``
answered ``0`` for every field on every run — a wrong number rather than a
missing one, which is the kind of thing nobody notices until someone reads the
usage panel and believes it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.gateway.routers.thread_runs import _service_to_response
from deerflow.runtime import RunManager
from deerflow.runtime.runs.manager import RunRecord
from deerflow.runtime.runs.schemas import DisconnectMode, RunStatus
from deerflow.services.implementations import RunServiceImpl


def _record(**overrides) -> RunRecord:
    values = {
        "run_id": "run-1",
        "thread_id": "thread-1",
        "assistant_id": "lead-agent",
        "status": RunStatus.success,
        "on_disconnect": DisconnectMode.cancel,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:05:00+00:00",
        "model_name": "minimax-m3",
        "total_input_tokens": 1200,
        "total_output_tokens": 300,
        "total_tokens": 1500,
        "llm_call_count": 4,
        "lead_agent_tokens": 900,
        "subagent_tokens": 500,
        "middleware_tokens": 100,
        "message_count": 7,
    }
    values.update(overrides)
    return RunRecord(**values)


def _manager_returning(*records: RunRecord):
    manager = SimpleNamespace()
    manager.list_by_thread = lambda thread_id, *, user_id=None, limit=100: _async(records)
    manager.get = lambda run_id, *, user_id=None: _async(records[0] if records else None)
    return manager


async def _async(value):
    return value


def test_run_summary_carries_usage_counters():
    service = RunServiceImpl(_manager_returning(_record()))
    summaries = asyncio.run(service.list_by_thread("thread-1"))

    summary = summaries[0]
    assert summary.total_input_tokens == 1200
    assert summary.total_output_tokens == 300
    assert summary.total_tokens == 1500
    assert summary.llm_call_count == 4
    assert summary.lead_agent_tokens == 900
    assert summary.subagent_tokens == 500
    assert summary.middleware_tokens == 100
    assert summary.message_count == 7
    assert summary.updated_at == "2026-01-01T00:05:00+00:00"


def test_wire_response_carries_every_counter():
    service = RunServiceImpl(_manager_returning(_record()))
    summary = asyncio.run(service.list_by_thread("thread-1"))[0]

    body = _service_to_response(summary)
    assert body.total_input_tokens == 1200
    assert body.total_output_tokens == 300
    assert body.total_tokens == 1500
    assert body.llm_call_count == 4
    assert body.lead_agent_tokens == 900
    assert body.subagent_tokens == 500
    assert body.middleware_tokens == 100
    assert body.message_count == 7
    assert body.updated_at == "2026-01-01T00:05:00+00:00"


def test_run_detail_carries_every_counter():
    service = RunServiceImpl(_manager_returning(_record()))
    detail = asyncio.run(service.get("run-1"))

    assert detail is not None
    assert detail.total_input_tokens == 1200
    assert detail.llm_call_count == 4
    assert detail.message_count == 7
    assert detail.updated_at == "2026-01-01T00:05:00+00:00"


def test_counters_survive_a_store_round_trip():
    """A run read back from the store reports what was written, not zeros."""
    record = _record()
    row = {
        "run_id": record.run_id,
        "thread_id": record.thread_id,
        "assistant_id": record.assistant_id,
        "status": record.status.value,
        "on_disconnect": record.on_disconnect.value,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "total_input_tokens": record.total_input_tokens,
        "total_output_tokens": record.total_output_tokens,
        "total_tokens": record.total_tokens,
        "llm_call_count": record.llm_call_count,
        "lead_agent_tokens": record.lead_agent_tokens,
        "subagent_tokens": record.subagent_tokens,
        "middleware_tokens": record.middleware_tokens,
        "message_count": record.message_count,
    }
    hydrated = RunManager._record_from_store(row)
    assert hydrated.total_tokens == 1500
    assert hydrated.message_count == 7
