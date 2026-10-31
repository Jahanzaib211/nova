"""Terminal output must arrive while a command runs, not only when it exits.

``bash`` used to call ``execute_command()``, which blocks to exit, and then wrote
a single observation line. In the live run that made ``npm install`` appear in
the Agent's Computer Terminal as one row at completion -- minutes of silence,
then a wall of text.

**The mechanism is not the obvious one, and these tests pin why.** Polling
``shell.view`` after ``exec_command(async_mode=True)`` does not stream: measured
against the live container, ``view`` reports ``output=''`` for the entire run and
only yields the text at ``status='completed'``. An interactive PTY
(``write_to_process``) buffers identically. So the command's output is
redirected to a file and the *file* API is polled, which does deliver
incrementally::

    t+0.04s status='running' file='line 1\n'
    t+1.25s status='running' file='line 1\nline 2\n'
    t+2.46s status='running' file='line 1\nline 2\nline 3\n'

Verified end-to-end against a real sandbox: three chunks at t+0.72s, t+1.53s and
t+2.34s for a command that ran 4.59s.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# `deerflow.sandbox.tools` and `deerflow.tools.builtins.workspace_tools` import
# each other, so importing the former *first* in a fresh interpreter raises
# ImportError. Pre-existing, and unrelated to streaming -- entering from the
# workspace_tools side is the order the application itself uses.
import deerflow.tools.builtins.workspace_tools  # noqa: F401,E402  isort:skip
from deerflow.sandbox import tools as sandbox_tools  # noqa: E402  isort:skip


@pytest.fixture()
def sandbox():
    with patch("deerflow.community.aio_sandbox.aio_sandbox.AioSandboxClient"):
        from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox

        sb = AioSandbox(id="test-sandbox", base_url="http://localhost:8080")
        # Polling delay is real time; the fake frames below are already ready.
        sb._STREAM_POLL_INTERVAL = 0
        return sb


def _stream(sandbox, frames: list[tuple[str, str]]) -> None:
    """Drive one poll cycle per frame: (output-file contents, session status).

    The producer reads the file first and checks the status second, so each
    frame is one full iteration of its loop.
    """
    state = {"i": 0}

    def read_file(*, file, **kwargs):  # noqa: A002 - matches the SDK keyword
        content = frames[min(state["i"], len(frames) - 1)][0]
        return SimpleNamespace(data=SimpleNamespace(content=content))

    def view(*, id, **kwargs):  # noqa: A002 - matches the SDK keyword
        status = frames[min(state["i"], len(frames) - 1)][1]
        state["i"] += 1
        return SimpleNamespace(data=SimpleNamespace(status=status))

    sandbox._client.file.read_file = read_file
    sandbox._client.shell.view = view


class TestChunksArriveBeforeExit:
    def test_output_is_reported_while_the_command_is_still_running(self, sandbox):
        _stream(
            sandbox,
            [
                ("line 1\n", "running"),
                ("line 1\nline 2\n", "running"),
                ("line 1\nline 2\nline 3\n", "completed"),
            ],
        )
        seen: list[tuple[str, bool]] = []
        out = sandbox.execute_command_streaming("seq 1 3", lambda t, r: seen.append((t, r)))

        assert [t for t, _ in seen] == ["line 1\n", "line 2\n", "line 3\n"]
        assert not any(replace for _, replace in seen), "plain growth must append"
        assert out == "line 1\nline 2\nline 3\n"

    def test_a_running_status_keeps_polling_until_terminal(self, sandbox):
        _stream(sandbox, [("a", "running"), ("ab", "running"), ("abc", "terminated")])
        seen: list[str] = []
        sandbox.execute_command_streaming("x", lambda t, r: seen.append(t))
        assert seen == ["a", "b", "c"]

    @pytest.mark.parametrize("status", ["completed", "no_change_timeout", "hard_timeout", "terminated"])
    def test_every_terminal_status_ends_the_poll(self, sandbox, status):
        """`running` is the only non-terminal state; anything else must stop the
        loop, or a timed-out command spins until the outer timeout."""
        _stream(sandbox, [("done", status)])
        sandbox.execute_command_streaming("x", lambda t, r: None)


class TestHowChangesAreClassified:
    def test_growth_is_reported_as_an_append(self, sandbox):
        _stream(sandbox, [("a", "running"), ("ab", "running"), ("abc", "completed")])
        seen: list[tuple[str, bool]] = []
        sandbox.execute_command_streaming("x", lambda t, r: seen.append((t, r)))
        assert seen == [("a", False), ("b", False), ("c", False)]

    def test_a_rewritten_file_is_reported_as_replace(self, sandbox):
        """The file should only ever grow, but a truncating writer must not have
        its output concatenated onto stale text."""
        _stream(sandbox, [("old content", "running"), ("fresh", "completed")])
        seen: list[tuple[str, bool]] = []
        out = sandbox.execute_command_streaming("x", lambda t, r: seen.append((t, r)))
        assert seen[-1] == ("fresh", True)
        assert out == "fresh"

    def test_unchanged_output_emits_nothing(self, sandbox):
        """A poll that sees no new bytes must not emit an empty frame."""
        _stream(sandbox, [("same", "running"), ("same", "running"), ("same", "completed")])
        seen: list[str] = []
        sandbox.execute_command_streaming("x", lambda t, r: seen.append(t))
        assert seen == ["same"]

    def test_a_file_that_does_not_exist_yet_is_not_an_error(self, sandbox):
        """The first poll of every command lands before the redirect is set up."""

        def read_file(*, file, **kwargs):
            raise RuntimeError("no such file")

        sandbox._client.file.read_file = read_file
        sandbox._client.shell.view = lambda **kw: SimpleNamespace(data=SimpleNamespace(status="completed"))
        assert sandbox.execute_command_streaming("x", lambda t, r: None) == "(no output)"

    def test_the_last_line_is_never_dropped(self, sandbox):
        """The final write lands between the last poll and the process exiting,
        so the producer reads once more after the loop breaks."""
        frames = [("line 1\n", "running"), ("line 1\nline 2\n", "completed")]
        _stream(sandbox, frames)
        seen: list[str] = []
        out = sandbox.execute_command_streaming("x", lambda t, r: seen.append(t))
        assert "".join(seen) == "line 1\nline 2\n"
        assert out == "line 1\nline 2\n"


class TestItStillBehavesLikeExecuteCommand:
    @pytest.mark.parametrize(
        "command",
        [
            "echo a\necho b",
            "cat <<EOF\nhello\nworld\nEOF",
            'echo "the agent\'s host process!"',
            "echo single",
        ],
        ids=["multiline", "heredoc", "bang-in-quotes", "single-line"],
    )
    def test_the_script_goes_on_the_wire_base64_encoded(self, sandbox, command):
        """Streaming must compose with the P0 fix, not bypass it -- and it wraps
        single-line commands too, which `execute_command` deliberately does not.

        The redirect is why. Appending `> file 2>&1` to raw text binds it to the
        *last* command only, so `echo a; echo b` would stream half its output
        and return the other half. One `bash -s` makes it unambiguous.
        """
        sent = []

        def exec_command(command, **kwargs):
            sent.append(command)
            return SimpleNamespace(data=SimpleNamespace(output="", status="running"))

        sandbox._client.shell.exec_command = exec_command
        _stream(sandbox, [("ok", "completed")])
        sandbox.execute_command_streaming(command, lambda t, r: None)

        wire = sent[0]
        assert "\n" not in wire, "a newline reached the sandbox"
        encoded = wire.split("echo ", 1)[1].split(" |", 1)[0]
        assert base64.b64decode(encoded).decode("utf-8") == command, "payload must be byte-exact"
        assert "| base64 -d | bash -s > " in wire and wire.endswith(" 2>&1")

    def test_a_sequenced_command_redirects_as_a_whole(self, sandbox):
        """The bug a naive `cmd > file` would have: only `echo b` redirected."""
        wire = sandbox._stream_wire("echo a; echo b", "/tmp/out.log")
        encoded = wire.split("echo ", 1)[1].split(" |", 1)[0]
        assert base64.b64decode(encoded).decode() == "echo a; echo b"
        assert wire.endswith("| bash -s > /tmp/out.log 2>&1")

    def test_the_log_path_is_shell_quoted(self, sandbox):
        wire = sandbox._stream_wire("echo hi", "/tmp/a b.log")
        assert "'/tmp/a b.log'" in wire

    def test_a_start_failure_degrades_to_the_blocking_path(self, sandbox):
        """A command must never be lost because streaming was unavailable."""

        def boom(**kwargs):
            raise RuntimeError("no session for you")

        sandbox._client.shell.create_session = boom
        with patch.object(type(sandbox), "_execute_command_locked", return_value="blocking result"):
            out = sandbox.execute_command_streaming("echo hi", lambda t, r: None)
        assert out == "blocking result"

    def test_no_output_matches_execute_command(self, sandbox):
        _stream(sandbox, [("", "completed")])
        assert sandbox.execute_command_streaming("true", lambda t, r: None) == "(no output)"

    def test_error_observation_falls_back_to_the_retrying_path(self, sandbox):
        from deerflow.community.aio_sandbox.aio_sandbox import _ERROR_OBSERVATION_SIGNATURE

        _stream(sandbox, [(f"Command failed: {_ERROR_OBSERVATION_SIGNATURE}", "completed")])
        with patch.object(type(sandbox), "_execute_command_locked", return_value="recovered"):
            assert sandbox.execute_command_streaming("x", lambda t, r: None) == "recovered"

    def test_the_per_command_session_is_always_released(self, sandbox):
        released: list[str] = []
        sandbox._client.shell.cleanup_session = lambda sid: released.append(sid)
        _stream(sandbox, [("out", "completed")])
        sandbox.execute_command_streaming("x", lambda t, r: None)
        assert len(released) == 1 and released[0].startswith("nova-stream-")

    def test_the_output_file_is_removed(self, sandbox):
        """One file per command in the sandbox's /tmp, so they must not pile up."""
        sent: list[str] = []

        def exec_command(command, **kwargs):
            sent.append(command)
            return SimpleNamespace(data=SimpleNamespace(output="", status="running"))

        sandbox._client.shell.exec_command = exec_command
        _stream(sandbox, [("out", "completed")])
        sandbox.execute_command_streaming("x", lambda t, r: None)

        removals = [c for c in sent if c.startswith("rm -f ")]
        assert len(removals) == 1, "the redirect target must be cleaned up"
        assert "/tmp/nova-stream-" in removals[0]

    def test_cleanup_still_runs_when_the_command_blows_up(self, sandbox):
        released: list[str] = []
        sandbox._client.shell.cleanup_session = lambda sid: released.append(sid)
        sandbox._client.shell.view = lambda **kw: (_ for _ in ()).throw(RuntimeError("gone"))
        sandbox._client.file.read_file = lambda **kw: SimpleNamespace(data=SimpleNamespace(content="partial"))
        out = sandbox.execute_command_streaming("x", lambda t, r: None)
        assert out == "partial", "output seen before the failure is still returned"
        assert len(released) == 1

    def test_tailing_stops_once_the_output_is_absurd(self, sandbox):
        """A runaway log must not be re-read on every poll -- the file API
        returns whole files, so that is quadratic in bytes."""
        sandbox._STREAM_MAX_TAIL_BYTES = 10
        reads = {"n": 0}

        def read_file(*, file, **kwargs):
            reads["n"] += 1
            return SimpleNamespace(data=SimpleNamespace(content="x" * 50))

        statuses = iter(["running", "running", "running", "completed"])
        sandbox._client.file.read_file = read_file
        sandbox._client.shell.view = lambda **kw: SimpleNamespace(data=SimpleNamespace(status=next(statuses, "completed")))
        out = sandbox.execute_command_streaming("yes", lambda t, r: None)

        # One read to pass the cap, then no more until the single final read.
        assert reads["n"] == 2, f"kept tailing past the cap ({reads['n']} reads)"
        assert out == "x" * 50, "the full output is still returned"


class TestNonStreamingBackendsAreUnaffected:
    def test_the_base_class_does_not_claim_to_stream(self):
        from deerflow.sandbox.sandbox import Sandbox

        assert Sandbox.supports_streaming is False

    def test_a_non_streaming_backend_writes_one_plain_line(self, monkeypatch):
        """`supports_streaming = False` must keep the single-observation shape:
        no id, no state, exactly what every other tool still writes."""
        lines: list[dict] = []

        def capture(sandbox_id, tool, path, summary, output="", **kw):
            lines.append({"summary": summary, "output": output, **kw})

        monkeypatch.setattr(sandbox_tools, "_write_sandbox_observation", capture)

        class Plain:
            supports_streaming = False

            def execute_command(self, command):
                return "plain output"

        sandbox = Plain()
        # The exact branch bash_tool takes for a non-streaming backend.
        if getattr(sandbox, "supports_streaming", False):
            raise AssertionError("a plain backend must not be treated as streaming")
        raw = sandbox.execute_command("echo hi")
        sandbox_tools._write_sandbox_observation("sb", "bash", None, "$ echo hi", raw)

        assert len(lines) == 1
        assert lines[0] == {"summary": "$ echo hi", "output": "plain output"}, "no streaming keys on a plain line"


class TestObservationFrames:
    def test_frames_share_one_id_and_close_with_done(self, monkeypatch):
        lines: list[dict] = []

        def capture(sandbox_id, tool, path, summary, output="", **kw):
            lines.append({"summary": summary, "output": output, **kw})

        monkeypatch.setattr(sandbox_tools, "_write_sandbox_observation", capture)

        class Streaming:
            supports_streaming = True

            def execute_command_streaming(self, command, on_chunk):
                on_chunk("hello ", False)
                on_chunk("world", False)
                return "hello world"

        out = sandbox_tools._stream_bash_observations(Streaming(), "sb-1", "echo hello world")

        assert out == "hello world"
        ids = {line["obs_id"] for line in lines}
        assert len(ids) == 1, "every frame of one command must share an id"
        assert lines[0]["state"] == "running" and lines[0]["summary"] == "$ echo hello world"
        assert [line.get("delta") for line in lines[1:3]] == ["hello ", "world"]
        assert lines[-1]["state"] == "done" and lines[-1]["output"] == "hello world"

    def test_a_replace_chunk_is_written_as_replace(self, monkeypatch):
        lines: list[dict] = []
        monkeypatch.setattr(
            sandbox_tools,
            "_write_sandbox_observation",
            lambda *a, **kw: lines.append(kw),
        )

        class Redrawing:
            supports_streaming = True

            def execute_command_streaming(self, command, on_chunk):
                on_chunk("50%", True)
                return "100%"

        sandbox_tools._stream_bash_observations(Redrawing(), "sb-1", "pip install x")
        assert any(line.get("replace") == "50%" for line in lines)
        assert not any("delta" in line for line in lines)

    def test_a_streaming_backend_that_throws_still_completes_the_command(self, monkeypatch):
        monkeypatch.setattr(sandbox_tools, "_write_sandbox_observation", lambda *a, **kw: None)

        class Broken:
            supports_streaming = True

            def execute_command_streaming(self, command, on_chunk):
                raise RuntimeError("stream died")

            def execute_command(self, command):
                return "fallback output"

        assert sandbox_tools._stream_bash_observations(Broken(), "sb-1", "x") == "fallback output"


class TestObservationTruncation:
    """The Terminal used to show silently less than the model received."""

    def test_output_is_truncated_with_an_explicit_marker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox_tools, "_thread_id_for_observation", lambda sid: "t1")
        monkeypatch.setattr(sandbox_tools, "_observation_max_chars", lambda: 200)

        class _Paths:
            def thread_dir(self, thread_id, user_id=None):
                return tmp_path

        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: _Paths())
        monkeypatch.setattr("deerflow.runtime.user_context.get_effective_user_id", lambda: None)

        sandbox_tools._write_sandbox_observation("sb", "bash", None, "$ x", "A" * 5000)

        record = json.loads((tmp_path / "sandbox.log").read_text().splitlines()[-1])
        assert len(record["output"]) <= 200
        assert "truncated" in record["output"], "a silent cut is what this replaces"

    def test_the_limit_comes_from_config_not_a_literal(self):
        from deerflow.config.sandbox_config import SandboxConfig

        assert SandboxConfig(use="test").observation_max_chars == 20000, "must match bash_output_max_chars, not the old 2000"

    def test_a_line_without_an_id_keeps_exactly_its_old_shape(self, tmp_path, monkeypatch):
        """Back-compat: existing tools and older frontends must be unaffected."""
        monkeypatch.setattr(sandbox_tools, "_thread_id_for_observation", lambda sid: "t1")

        class _Paths:
            def thread_dir(self, thread_id, user_id=None):
                return tmp_path

        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: _Paths())
        monkeypatch.setattr("deerflow.runtime.user_context.get_effective_user_id", lambda: None)

        sandbox_tools._write_sandbox_observation("sb", "write_file", "/mnt/x", "Wrote 3 bytes", "abc")

        record = json.loads((tmp_path / "sandbox.log").read_text().splitlines()[-1])
        assert set(record) == {"ts", "type", "path", "summary", "output"}
