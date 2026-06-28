"""Generic build-journal tests (project-agnostic).

The build journal must work deterministically for ANY project — it is derived
purely from the agent's tool name + args, with zero project/framework assumptions.
These fixtures use throwaway names only.
"""

from __future__ import annotations

from collections import deque

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares import observe_adjust_middleware as oam
from deerflow.agents.middlewares.observe_adjust_middleware import ObserveAdjustMiddleware


def _cycle(tool_name: str, *, args: dict, content: str = "ok", tool_call_id: str = "c1") -> list:
    """A minimal AIMessage(tool_call) + ToolMessage(result) pair."""
    ai = AIMessage(
        content="",
        tool_calls=[{"id": tool_call_id, "name": tool_name, "args": args}],
    )
    tm = ToolMessage(content=content, name=tool_name, tool_call_id=tool_call_id)
    return [ai, tm]


def test_write_file_line_is_generic():
    line = oam._derive_journal_line(_cycle("write_file", args={"path": "/x/y/thing.tsx"}))
    assert line is not None
    assert "wrote thing.tsx" in line


def test_bash_line_uses_command():
    line = oam._derive_journal_line(_cycle("bash", args={"command": "npm install"}))
    assert line is not None
    assert "$ npm install" in line


def test_dev_verify_pass_and_issues():
    ok = oam._derive_journal_line(_cycle("dev_verify", args={}, content="Verdict: ✅ PASS"))
    bad = oam._derive_journal_line(_cycle("dev_verify", args={}, content="Verdict: ⚠️ ISSUES — fix"))
    assert ok is not None and "dev_verify → PASS" in ok
    assert bad is not None and "dev_verify → ISSUES" in bad


def test_present_files_is_journaled():
    line = oam._derive_journal_line(_cycle("present_files", args={}))
    assert line is not None and "presented files" in line


def test_readonly_tools_are_skipped():
    # read-only / search tools are noise — not journaled.
    assert oam._derive_journal_line(_cycle("read_file", args={"path": "/x/thing.tsx"})) is None
    assert oam._derive_journal_line(_cycle("grep_files", args={"pattern": "foo"})) is None


def test_record_event_populates_buffer_and_dedups(monkeypatch):
    written: list[list[str]] = []
    monkeypatch.setattr(oam, "_write_journal_file", lambda tid, lines: written.append(list(lines)))
    oam._journals.pop("tid-rec", None)

    oam._record_journal_event("tid-rec", _cycle("write_file", args={"path": "a.py"}))
    oam._record_journal_event("tid-rec", _cycle("write_file", args={"path": "a.py"}))  # dup → ignored
    oam._record_journal_event("tid-rec", _cycle("bash", args={"command": "ls"}))

    buf = oam._journals["tid-rec"]
    assert len(buf) == 2  # dedup collapsed the identical consecutive write
    assert written and "$ ls" in written[-1][-1]
    oam._journals.pop("tid-rec", None)


def test_record_event_noop_without_thread_id(monkeypatch):
    monkeypatch.setattr(oam, "_write_journal_file", lambda tid, lines: None)
    # Should not raise and should not create an entry.
    oam._record_journal_event(None, _cycle("write_file", args={"path": "a.py"}))
    assert None not in oam._journals


class _FakeRuntime:
    def __init__(self, ctx: dict) -> None:
        self.context = ctx


class _FakeRequest:
    def __init__(self, messages: list, runtime: _FakeRuntime) -> None:
        self.messages = messages
        self.runtime = runtime

    def override(self, *, messages: list) -> "_FakeRequest":
        self.messages = messages
        return self


def test_inject_appends_build_journal_when_active():
    oam._journals["tid-inj"] = deque(["- 10:00:00  wrote thing.tsx"], maxlen=oam._BUILD_JOURNAL_MAX_LINES)
    mw = ObserveAdjustMiddleware()
    req = _FakeRequest([HumanMessage(content="continue")], _FakeRuntime({"thread_id": "tid-inj"}))

    out = mw._inject_journal(req)

    last = out.messages[-1]
    assert isinstance(last, HumanMessage)
    assert last.name == "build_journal"
    assert "<build_journal>" in last.content
    assert "wrote thing.tsx" in last.content
    oam._journals.pop("tid-inj", None)


def test_inject_noop_for_plain_chat():
    # No journal for this thread → plain Q&A is untouched (no reminder appended).
    oam._journals.pop("tid-empty", None)
    mw = ObserveAdjustMiddleware()
    req = _FakeRequest([HumanMessage(content="hi")], _FakeRuntime({"thread_id": "tid-empty"}))

    out = mw._inject_journal(req)

    assert len(out.messages) == 1
    assert out.messages[0].content == "hi"
