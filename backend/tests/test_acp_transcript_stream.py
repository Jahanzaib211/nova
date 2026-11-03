"""The ACP transcript must read like a transcript.

Two live failures shaped these tests. Every ``agent_thought_chunk`` became its
own ``thinking: <fragment>`` status, so a delegated Claude Code turn rendered
as dozens of rows like ``thinking: ks like a system-level set`` — and since the
UI keeps only the last 50 statuses, the fragments pushed every real tool call
out of view. And a policy denial reached the transcript as a bare
``permission denied: edit — Write``, indistinguishable from a person clicking
"deny"; the agent itself read the cancel as "the user aborted" and asked the
user to approve a dialog that does not exist.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from nova.config.acp_config import ACPAgentConfig
from nova.runtimes import acp_transport
from nova.runtimes.acp_transport import _ThoughtBuffer, permission_denial_reason
from nova.runtimes.types import policy_for_mode

# --- _ThoughtBuffer --------------------------------------------------------


def _buffer(max_chars: int = 500):
    out: list[tuple[str, str]] = []
    return _ThoughtBuffer(lambda sid, text: out.append((sid, text)), max_chars=max_chars), out


def test_fragments_become_one_status_on_flush():
    buf, out = _buffer()
    for piece in ["I", "'m checking whether the", " Write fail", "ure is really about", " the session."]:
        buf.push("s1", piece)
    assert out == []
    buf.flush()
    assert out == [("s1", "thinking: I'm checking whether the Write failure is really about the session.")]


def test_a_paragraph_boundary_flushes_the_finished_paragraph():
    buf, out = _buffer()
    buf.push("s1", "First thought, done.\n\nSecond")
    assert out == [("s1", "thinking: First thought, done.")]
    buf.push("s1", " thought.")
    buf.flush()
    assert out[-1] == ("s1", "thinking: Second thought.")


def test_the_size_bound_flushes_on_a_word_boundary_and_drops_nothing():
    buf, out = _buffer(max_chars=40)
    words = [f"word{i} " for i in range(40)]
    for w in words:
        buf.push("s1", w)
    buf.flush()
    assert len(out) > 1
    bodies = [text.removeprefix("thinking: ") for _, text in out]
    assert all(len(b) <= 40 for b in bodies)
    # No word is split across two statuses, and none is lost or reordered.
    assert " ".join(bodies).split() == "".join(words).split()


def test_a_single_long_token_still_flushes():
    buf, out = _buffer(max_chars=10)
    buf.push("s1", "x" * 25)
    buf.flush()
    assert "".join(t.removeprefix("thinking: ") for _, t in out) == "x" * 25


def test_switching_session_flushes_the_previous_one():
    buf, out = _buffer()
    buf.push("s1", "about s1")
    buf.push("s2", "about s2")
    assert out == [("s1", "thinking: about s1")]
    buf.flush()
    assert out[-1] == ("s2", "thinking: about s2")


def test_whitespace_only_thought_emits_nothing():
    buf, out = _buffer()
    buf.push("s1", "  \n ")
    buf.flush()
    buf.flush()
    assert out == []


# --- permission_denial_reason ---------------------------------------------


def test_reason_for_a_kind_outside_the_allow_list():
    reason = permission_denial_reason("edit", policy_for_mode("standard"))
    assert reason == "policy: kind 'edit' is not allowed under Standard"


def test_reason_for_an_always_denied_kind():
    reason = permission_denial_reason("delete", policy_for_mode("full"))
    assert reason == "policy: kind 'delete' is always denied under Default (Full Access)"


def test_reason_for_an_unlabelled_request():
    assert "did not say what kind" in permission_denial_reason(None, policy_for_mode("standard"))


def test_reason_for_a_nova_operation():
    reason = permission_denial_reason("other", policy_for_mode("plan"), nova_op_kind="write")
    assert reason == "policy: Nova 'write' operations are not allowed under Plan (read-only)"


def test_reason_when_the_agent_offered_no_allow_option():
    reason = permission_denial_reason("read", policy_for_mode("standard"), offered_allow=False)
    assert "offered no allow option" in reason


# --- wired through run_acp_prompt -----------------------------------------


class _TextContentBlock:
    def __init__(self, text):
        self.text = text


def _thought(text):
    return SimpleNamespace(content=_TextContentBlock(text), session_update="agent_thought_chunk")


def _message(text):
    return SimpleNamespace(content=_TextContentBlock(text), session_update="agent_message_chunk")


def _tool(kind, title):
    return SimpleNamespace(session_update="tool_call", kind=kind, title=title)


def _install_fake_acp(monkeypatch, script):
    """``script(client)`` plays the adapter's side of one prompt."""
    holder: dict = {}

    class _Proc:
        returncode = 0
        pid = 999999

        async def wait(self):
            return 0

    class Conn:
        async def initialize(self, **kwargs):
            return SimpleNamespace()

        async def new_session(self, **kwargs):
            return SimpleNamespace(session_id="s-1")

        async def prompt(self, **kwargs):
            await script(holder["client"])
            return SimpleNamespace()

    class Ctx:
        async def __aenter__(self):
            return Conn(), _Proc()

        async def __aexit__(self, *exc):
            return False

    def spawn(client, cmd, *args, **kwargs):
        holder["client"] = client
        return Ctx()

    monkeypatch.setitem(
        sys.modules,
        "acp",
        SimpleNamespace(
            PROTOCOL_VERSION="2026-03-24",
            Client=object,
            spawn_agent_process=spawn,
            text_block=lambda text: {"type": "text", "text": text},
            RequestPermissionResponse=lambda outcome: SimpleNamespace(outcome=outcome),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "acp.schema",
        SimpleNamespace(
            ClientCapabilities=lambda **k: SimpleNamespace(),
            Implementation=lambda **k: SimpleNamespace(),
            TextContentBlock=_TextContentBlock,
            AllowedOutcome=lambda outcome, optionId: SimpleNamespace(outcome=outcome, optionId=optionId),
            DeniedOutcome=lambda outcome: SimpleNamespace(outcome=outcome),
        ),
    )


ALLOW_OPTIONS = [SimpleNamespace(kind="allow_once", option_id="a1"), SimpleNamespace(kind="reject_once", option_id="r1")]


async def _run(mode="standard"):
    statuses: list[str] = []
    answer = await acp_transport.run_acp_prompt(
        ACPAgentConfig(command="npx", args=["-y", "pkg"], description="test adapter"),
        "hello",
        cwd="/tmp",
        mcp_servers=[],
        permission=policy_for_mode(mode),
        on_text=lambda *a: None,
        on_status=lambda _sid, s: statuses.append(s),
    )
    return answer, statuses


@pytest.mark.asyncio
async def test_thoughts_coalesce_and_keep_their_place_between_actions(monkeypatch):
    async def script(client):
        await client.session_update("s-1", _thought("Let me "))
        await client.session_update("s-1", _thought("check."))
        await client.session_update("s-1", _tool("read", "Read(x)"))
        await client.session_update("s-1", _thought("Now "))
        await client.session_update("s-1", _thought("write."))
        await client.request_permission(ALLOW_OPTIONS, "s-1", SimpleNamespace(kind="edit", title="Write", tool_call_id="t1"))
        await client.session_update("s-1", _message("done"))

    _install_fake_acp(monkeypatch, script)
    answer, statuses = await _run()
    assert answer == "done"
    assert statuses == [
        "thinking: Let me check.",
        "read: Read(x)",
        "thinking: Now write.",
        "permission denied: edit — Write (policy: kind 'edit' is not allowed under Standard)",
    ]


@pytest.mark.asyncio
async def test_an_approved_request_keeps_its_plain_status(monkeypatch):
    async def script(client):
        await client.request_permission(ALLOW_OPTIONS, "s-1", SimpleNamespace(kind="read", title="Read(a)", tool_call_id="t1"))

    _install_fake_acp(monkeypatch, script)
    _, statuses = await _run()
    assert statuses == ["permission approved: read — Read(a)"]


@pytest.mark.asyncio
async def test_trailing_thought_is_flushed_at_end_of_turn(monkeypatch):
    async def script(client):
        await client.session_update("s-1", _message("answer"))
        await client.session_update("s-1", _thought("one last "))
        await client.session_update("s-1", _thought("reflection"))

    _install_fake_acp(monkeypatch, script)
    _, statuses = await _run()
    assert statuses == ["thinking: one last reflection"]


@pytest.mark.asyncio
async def test_thought_is_flushed_even_when_the_turn_fails(monkeypatch):
    async def script(client):
        await client.session_update("s-1", _thought("halfway through"))
        raise RuntimeError("adapter blew up")

    _install_fake_acp(monkeypatch, script)
    statuses: list[str] = []
    with pytest.raises(RuntimeError, match="adapter blew up"):
        await acp_transport.run_acp_prompt(
            ACPAgentConfig(command="npx", args=[], description="test adapter"),
            "hello",
            cwd="/tmp",
            mcp_servers=[],
            permission=policy_for_mode("standard"),
            on_text=lambda *a: None,
            on_status=lambda _sid, s: statuses.append(s),
        )
    assert statuses == ["thinking: halfway through"]


def test_the_denial_status_still_parses_for_the_activity_mirror(monkeypatch, tmp_path):
    """middleware._mirror_runtime_action keys off ``permission denied: <kind> — <title>``."""
    import json

    from nova.config import paths as paths_module
    from nova.runtimes import middleware

    monkeypatch.setenv("NOVA_HOME", str(tmp_path))
    monkeypatch.setattr(paths_module, "_paths", None, raising=False)
    monkeypatch.setattr(paths_module, "_paths_singleton", None, raising=False)

    middleware._mirror_runtime_action("thread-denial-1", "claude_code", "permission denied: edit — Write (policy: kind 'edit' is not allowed under Standard)")
    [log] = list(tmp_path.rglob("sandbox.log"))
    [line] = [json.loads(raw) for raw in log.read_text().splitlines() if raw.strip()]
    assert line["type"] == "acp_permission_denied"
    assert line["summary"] == "claude_code: edit — Write (policy: kind 'edit' is not allowed under Standard)"
