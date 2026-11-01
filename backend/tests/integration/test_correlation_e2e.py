"""Phase C0.7 — end-to-end correlation_id test via the live gateway SSE.

Boots the gateway through Starlette's TestClient (the shared
``isolated_app`` harness from ``tests/conftest.py``), creates a real run via
``POST /api/threads/{tid}/runs/stream``, and asserts:

1. The HTTP response carries the SSE content-type.
2. The first frame yielded by the SSE stream is the
   ``: correlation_id=<hex>\\n\\n`` comment.
3. The correlation_id is exactly 32 lowercase hex characters (``uuid4().hex``).
4. The same correlation_id is registered for the run in the diagnostics
   registry, and a diagnostics record for the run is stamped with it.
5. The correlation_id round-trips through the persistent RunStore and is
   exposed on the runs API (``GET /runs`` and ``GET /runs/{id}``).

Only the agent graph is replaced (by a one-chunk scripted double); the app,
auth, ``start_run()``, ``run_agent()``, StreamBridge, SSE consumer and sqlite
RunStore are production code. No SDK, browser or external service.
"""

from __future__ import annotations

import functools
import re
from typing import Any
from unittest.mock import patch

import pytest
from support.gateway_app import create_thread, drain_stream, register_user, run_body, run_id_from_response

pytestmark = pytest.mark.no_auto_user

# UUIDv4 hex = 32 lowercase hex chars, no dashes.
HEX32 = re.compile(r"^[0-9a-f]{32}$")
FIRST_FRAME = re.compile(r"^: correlation_id=([^\n]*)\n\n")


class _OneChunkAgent:
    """Minimal ``run_agent`` double: yields one ``values`` state and finishes."""

    def __init__(self) -> None:
        self.checkpointer: Any | None = None
        self.store: Any | None = None
        self.metadata = {"model_name": "fake-test-model"}
        self.interrupt_before_nodes = None
        self.interrupt_after_nodes = None

    async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
        del graph_input, config, subgraphs
        state = {"messages": [], "title": "Corr Test"}
        yield (stream_mode[0], state) if isinstance(stream_mode, list) else state


def _agent_factory(*, config):
    del config
    return _OneChunkAgent()


def test_create_run_assigns_correlation_id(isolated_app, monkeypatch: pytest.MonkeyPatch):
    from starlette.testclient import TestClient

    from deerflow.runtime.stream_bridge import diagnostics as diagnostics_module

    with (
        patch("app.gateway.services.resolve_agent_factory", return_value=_agent_factory),
        TestClient(isolated_app) as client,
    ):
        csrf_token = register_user(client, email="corr-e2e@example.com")
        thread_id = create_thread(client, csrf_token)

        with client.stream(
            "POST",
            f"/api/threads/{thread_id}/runs/stream",
            json=run_body(),
            headers={"X-CSRF-Token": csrf_token},
        ) as response:
            assert response.status_code == 200, response.read().decode()
            assert response.headers["content-type"].startswith("text/event-stream")
            run_id = run_id_from_response(response)
            transcript = drain_stream(response)

        # (2) + (3): the very first frame is the correlation comment, 32-hex.
        match = FIRST_FRAME.match(transcript)
        assert match, f"first SSE frame is not the correlation_id comment: {transcript[:200]!r}"
        cid = match.group(1)
        assert HEX32.match(cid), f"correlation_id not 32-hex: {cid!r}"
        assert transcript.count(": correlation_id=") == 1

        # (4) diagnostics: the RunManager registered the run's id, and a record
        # emitted for this run (tracing enabled) is stamped with the same value.
        assert diagnostics_module.lookup_correlation_id(run_id=run_id) == cid
        assert diagnostics_module.lookup_correlation_id(thread_id=thread_id) == cid
        monkeypatch.setenv("DEER_FLOW_STREAM_TRACE", "1")
        monkeypatch.delenv("DEER_FLOW_STREAM_TRACE_FILE", raising=False)
        recorder = diagnostics_module._Diagnostics()
        recorder.record("correlation_e2e_probe", run_id=run_id)
        [record] = recorder.read_recent()
        assert record["run_id"] == run_id
        assert record["correlation_id"] == cid

        # (5) RunStore round-trip: the persisted row carries the same id ...
        store = isolated_app.state.run_store
        row = client.portal.call(functools.partial(store.get, run_id, user_id=None))
        assert row is not None, "run was not persisted to the RunStore"
        assert row["correlation_id"] == cid

        # ... and the runs API exposes it, for list and detail.
        runs = client.get(f"/api/threads/{thread_id}/runs")
        assert runs.status_code == 200, runs.text
        [listed] = [r for r in runs.json() if r["run_id"] == run_id]
        assert listed["correlation_id"] == cid

        detail = client.get(f"/api/threads/{thread_id}/runs/{run_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["correlation_id"] == cid


@pytest.mark.anyio
async def test_correlation_id_is_uuid_v4_format():
    """The correlation_id format is documented as ``uuid.uuid4().hex``
    (32 lowercase hex). This test guards the contract directly against
    the manager, for both creation paths.
    """
    from deerflow.runtime.runs.manager import RunManager

    mgr = RunManager()
    record = await mgr.create("thread-a", assistant_id="lead_agent")
    assert HEX32.match(record.correlation_id), f"correlation_id must be 32 hex chars, got {record.correlation_id!r}"

    rejected_path = await mgr.create_or_reject("thread-b", assistant_id="lead_agent")
    assert HEX32.match(rejected_path.correlation_id), f"create_or_reject correlation_id must be 32 hex chars, got {rejected_path.correlation_id!r}"

    # Variant value across runs — generate 5 and confirm all unique.
    cids = {(await mgr.create(f"thread-{i}")).correlation_id for i in range(5)}
    cids |= {record.correlation_id, rejected_path.correlation_id}
    assert len(cids) == 7, "correlation_id must be unique per run"
