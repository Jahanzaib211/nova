"""Incremental terminal frames must not reach a client that didn't ask for them.

This is the regression test for a live outage I caused. The `delta`/`replace`
frames were emitted as ordinary SSE `message` events carrying extra JSON fields,
on the theory that additive fields are backward compatible. They are not, when
the consumer renders whatever it is handed:

    if (parsed.type && parsed.ts !== undefined) { push(parsed); }   # deployed build

Every delta satisfied that guard, so each became a Terminal row with an empty
summary and empty output while its real text sat in a field that build never
read. Measured on the live thread: 54 blank rows out of 366 log lines, and
because they also consumed the 200-entry display window they broke Terminal,
Activity and Editor at once.

The backend hot-reloads; the frontend ships as a prebuilt bundle. Producer and
consumer therefore deploy independently, and compatibility cannot rest on a
guess about the consumer. A *named* SSE event is compatible by construction:
per the spec `EventSource.onmessage` receives only unnamed events, so any client
that does not explicitly subscribe ignores these — including every future one.
"""

from __future__ import annotations

import json

import pytest

from app.gateway.routers.sandbox import _SSE_DELTA_EVENT
from app.gateway.routers.sandbox import _classify_sse_frame as _frame_for


class TestNamedEventForIncrementalFrames:
    @pytest.mark.parametrize(
        "record",
        [
            {"ts": "1", "type": "bash", "id": "a", "delta": "out", "state": "running"},
            {"ts": "1", "type": "bash", "id": "a", "replace": "screen", "state": "running"},
        ],
        ids=["delta", "replace"],
    )
    def test_incremental_frames_get_the_named_event(self, record):
        frame = _frame_for(json.dumps(record))
        assert frame.startswith(f"event: {_SSE_DELTA_EVENT}\n"), "a client that does not subscribe must never receive this"

    @pytest.mark.parametrize(
        "record",
        [
            {"ts": "1", "type": "bash", "summary": "$ echo hi", "output": "hi"},
            {"ts": "1", "type": "write_file", "path": "/x", "summary": "Wrote 3 bytes", "output": ""},
            # An opening/closing streaming frame is a real event: it carries the
            # command line and the final output, so it stays on `message`.
            {"ts": "1", "type": "bash", "id": "a", "summary": "$ npm i", "state": "running"},
            {"ts": "1", "type": "bash", "id": "a", "summary": "$ npm i", "output": "done", "state": "done"},
        ],
        ids=["plain-bash", "write_file", "stream-open", "stream-close"],
    )
    def test_renderable_events_stay_on_the_default_event(self, record):
        frame = _frame_for(json.dumps(record))
        assert frame.startswith("data: "), "an old client must still see real events"
        assert "event:" not in frame

    def test_an_unparseable_line_is_not_hidden(self):
        """Better a client sees a line it cannot use than silently loses one."""
        frame = _frame_for("not json at all")
        assert frame.startswith("data: ")

    def test_a_line_merely_mentioning_delta_is_not_reclassified(self):
        """The cheap substring pre-check must be confirmed by a real parse."""
        record = {"ts": "1", "type": "bash", "summary": '$ echo "delta"', "output": '"replace"'}
        frame = _frame_for(json.dumps(record))
        assert frame.startswith("data: ")

    def test_every_frame_terminates_with_a_blank_line(self):
        """SSE framing: a frame is only dispatched after \\n\\n."""
        for line in ('{"ts":"1","type":"bash","id":"a","delta":"x"}', '{"ts":"1","type":"bash"}'):
            assert _frame_for(line).endswith("\n\n")


class TestTheProducerIsOffByDefault:
    """Belt and braces: even with the named event, streaming stays opt-in until
    the deployment's frontend is known to understand it."""

    def test_streaming_is_disabled_unless_configured(self):
        from deerflow.config.sandbox_config import SandboxConfig

        assert SandboxConfig(use="test").stream_terminal_output is False

    def test_bash_takes_the_plain_path_when_the_flag_is_off(self, monkeypatch):
        import deerflow.tools.builtins.workspace_tools  # noqa: F401 - import-cycle order
        from deerflow.sandbox import tools as sandbox_tools

        monkeypatch.setattr(sandbox_tools, "_terminal_streaming_enabled", lambda: False)
        assert sandbox_tools._terminal_streaming_enabled() is False
