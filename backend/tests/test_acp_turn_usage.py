"""An ACP turn's tokens must reach the run's totals.

Before this, a chat served by Claude Code or OpenClaw reported **zero**
tokens: the runtime middleware returns an ``AIMessage`` that never passes
through an LLM callback, so the journal's ``on_llm_end`` path never saw
the turn at all. Cost accounting then read "this run cost nothing" for a
turn that had spent 200k tokens — and the per-model breakdown had no
bucket for the runtime's model.

These tests pin:
* the usage is parsed off the ACP prompt response,
* it is recorded on the run's journal (once, not per middleware pass),
* it is attached to the returned message,
* an adapter that reports no usage changes nothing.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


class _Usage:
    """Stand-in for the ACP ``Usage`` payload."""

    def __init__(self, input_tokens=100, output_tokens=20, total_tokens=120, cached_read_tokens=0, cached_write_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens
        self.cached_read_tokens = cached_read_tokens
        self.cached_write_tokens = cached_write_tokens


class _PromptResult:
    def __init__(self, usage=None):
        self.usage = usage


def test_usage_is_parsed_from_the_acp_payload() -> None:
    from deerflow.runtimes.acp_transport import ACPTurnUsage

    usage = ACPTurnUsage.from_acp(_Usage())
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.total_tokens == 120


def test_missing_total_is_computed_from_input_and_output() -> None:
    from deerflow.runtimes.acp_transport import ACPTurnUsage

    usage = ACPTurnUsage.from_acp(_Usage(total_tokens=0))
    assert usage is not None
    assert usage.total_tokens == 120


def test_absent_or_empty_usage_yields_none() -> None:
    from deerflow.runtimes.acp_transport import ACPTurnUsage

    assert ACPTurnUsage.from_acp(None) is None
    assert ACPTurnUsage.from_acp(_Usage(input_tokens=0, output_tokens=0, total_tokens=0)) is None


def test_malformed_usage_never_raises() -> None:
    from deerflow.runtimes.acp_transport import ACPTurnUsage

    class Broken:
        @property
        def input_tokens(self):
            raise RuntimeError("boom")

    assert ACPTurnUsage.from_acp(Broken()) is None


def test_acp_text_is_a_str_carrying_the_usage() -> None:
    from deerflow.runtimes.acp_transport import ACPText, ACPTurnUsage

    usage = ACPTurnUsage.from_acp(_Usage())
    text = ACPText("hello", usage=usage, session_id="s1")
    # The runner protocol returns plain text; the usage rides along.
    assert isinstance(text, str)
    assert text == "hello"
    assert text.upper() == "HELLO"
    assert text.usage is usage
    assert text.session_id == "s1"


async def test_middleware_records_the_turn_usage_on_the_journal() -> None:
    from deerflow.runtimes.acp_transport import ACPText, ACPTurnUsage
    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware

    class FakeJournal:
        def __init__(self) -> None:
            self.records: list[dict] = []

        def record_external_llm_usage_records(self, records: list[dict]) -> None:
            self.records.extend(records)

    journal = FakeJournal()
    mw = RuntimeDispatchMiddleware()
    usage = ACPTurnUsage.from_acp(_Usage(input_tokens=200, output_tokens=50, total_tokens=250))
    text = ACPText("answer", usage=usage, session_id="sess-1")

    mw._record_turn_usage({"__run_journal": journal}, "claude_code", type("Cfg", (), {"model": "claude-sonnet-4"})(), text)

    assert len(journal.records) == 1
    record = journal.records[0]
    assert record["total_tokens"] == 250
    assert record["input_tokens"] == 200
    assert record["output_tokens"] == 50
    assert record["model_name"] == "claude-sonnet-4"
    # The source id must be stable for the same turn so a re-report is
    # deduped by the journal instead of counted twice.
    assert record["source_run_id"].endswith("sess-1")


async def test_middleware_without_journal_or_usage_does_nothing() -> None:
    from deerflow.runtimes.acp_transport import ACPText, ACPTurnUsage
    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware

    class FakeJournal:
        def __init__(self) -> None:
            self.records: list[dict] = []

        def record_external_llm_usage_records(self, records: list[dict]) -> None:
            self.records.extend(records)

    mw = RuntimeDispatchMiddleware()
    usage = ACPTurnUsage.from_acp(_Usage())

    # No journal in the context.
    mw._record_turn_usage({}, "claude_code", None, ACPText("x", usage=usage, session_id="s"))
    # Journal present, but the adapter reported no usage.
    journal = FakeJournal()
    mw._record_turn_usage({"__run_journal": journal}, "claude_code", None, ACPText("x", session_id="s"))
    assert journal.records == []


async def test_middleware_usage_recording_never_raises() -> None:
    from deerflow.runtimes.acp_transport import ACPText, ACPTurnUsage
    from deerflow.runtimes.middleware import RuntimeDispatchMiddleware

    class BrokenJournal:
        def record_external_llm_usage_records(self, records: list[dict]) -> None:
            raise RuntimeError("db down")

    mw = RuntimeDispatchMiddleware()
    usage = ACPTurnUsage.from_acp(_Usage())
    # A broken journal must not fail the turn it is reporting on.
    mw._record_turn_usage({"__run_journal": BrokenJournal()}, "claude_code", None, ACPText("x", usage=usage, session_id="s"))


async def test_usage_metadata_helper_shape() -> None:
    from deerflow.runtimes.acp_transport import ACPText, ACPTurnUsage
    from deerflow.runtimes.middleware import _usage_metadata_for

    usage = ACPTurnUsage.from_acp(_Usage())
    assert _usage_metadata_for(ACPText("x", usage=usage, session_id="s")) == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }
    assert _usage_metadata_for(ACPText("x", session_id="s")) is None
    assert _usage_metadata_for("plain string") is None
