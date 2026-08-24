"""Every `shell_*` tool must show up in the Agent's Computer Terminal.

`tool-surface.ts` routes all `shell_*` calls into the Terminal tab, but only
`shell_session` ever wrote a `sandbox.log` line -- and it logged the *start*
response. `shell_view`, `shell_wait`, `shell_write` and `shell_kill` wrote
nothing, so an interactive session did its work behind a blank panel: the agent
answered prompts nobody could see it being asked.

All five now share one observation id per session (``shell:<session_id>``), so a
session renders as a single growing Terminal entry rather than a scatter of
unrelated rows.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import deerflow.tools.builtins.workspace_tools as wt


@pytest.fixture(autouse=True)
def _clear_view_cache():
    """`_LAST_SHELL_VIEW` is module-level, so one test's screen would otherwise
    suppress the next test's first write."""
    wt._LAST_SHELL_VIEW.clear()
    yield
    wt._LAST_SHELL_VIEW.clear()


@pytest.fixture()
def observed(monkeypatch):
    """Capture observations instead of writing them to a thread directory."""
    lines: list[dict] = []

    def capture(sandbox_id, tool, path, summary, output="", **kw):
        lines.append({"tool": tool, "summary": summary, "output": output, **kw})

    monkeypatch.setattr(wt, "_write_sandbox_observation", capture)
    monkeypatch.setattr(wt, "_get_sandbox_id", lambda runtime: "sb-1")
    return lines


@pytest.fixture()
def client(monkeypatch):
    """A fake AIO client whose shell methods all succeed."""
    calls: list[str] = []

    shell = SimpleNamespace(
        create_session=lambda **kw: calls.append("create"),
        exec_command=lambda **kw: SimpleNamespace(data=SimpleNamespace(output="started")),
        view=lambda **kw: SimpleNamespace(data=SimpleNamespace(output="screen contents")),
        wait_for_process=lambda **kw: SimpleNamespace(data=SimpleNamespace(output="finished")),
        write_to_process=lambda **kw: calls.append("write"),
        kill_process=lambda **kw: calls.append("kill"),
    )
    fake = SimpleNamespace(shell=shell)
    monkeypatch.setattr(wt, "_aio_client_and_thread", lambda runtime: (fake, "t1", None))
    return fake


def _invoke(tool, **kwargs):
    """Call a LangChain tool with a dummy runtime."""
    return tool.func(runtime=SimpleNamespace(state={}), **kwargs)


class TestEachShellToolIsObserved:
    def test_shell_view_records_the_screen(self, observed, client):
        _invoke(wt.shell_view_tool, description="d", session_id="main")
        assert len(observed) == 1
        assert observed[0]["tool"] == "shell_view"
        # A screen is rendered state, not appended text.
        assert observed[0]["replace"] == "screen contents"
        assert "delta" not in observed[0]

    def test_shell_wait_closes_the_entry(self, observed, client):
        _invoke(wt.shell_wait_tool, description="d", session_id="main", seconds=5)
        assert observed[0]["tool"] == "shell_wait"
        assert observed[0]["state"] == "done"

    def test_shell_write_records_the_keystrokes(self, observed, client):
        _invoke(wt.shell_write_tool, description="d", input="yes\n", session_id="main")
        assert observed[0]["tool"] == "shell_write"
        assert "yes" in observed[0]["summary"]

    def test_shell_kill_closes_the_entry(self, observed, client):
        _invoke(wt.shell_kill_tool, description="d", session_id="main")
        assert observed[0]["tool"] == "shell_kill"
        assert observed[0]["state"] == "done"

    def test_shell_session_opens_the_entry(self, observed, client):
        _invoke(wt.shell_session_tool, description="d", command="npm run dev", session_id="main")
        assert observed[0]["tool"] == "shell_session"
        assert observed[0]["state"] == "running"


class TestOneSessionIsOneTerminalEntry:
    def test_every_tool_in_a_session_shares_one_id(self, observed, client):
        _invoke(wt.shell_session_tool, description="d", command="npm run dev", session_id="build")
        _invoke(wt.shell_view_tool, description="d", session_id="build")
        _invoke(wt.shell_wait_tool, description="d", session_id="build", seconds=1)
        _invoke(wt.shell_kill_tool, description="d", session_id="build")

        ids = {line["obs_id"] for line in observed}
        assert ids == {"shell:build"}, "one session must fold into one entry"

    def test_two_sessions_stay_separate(self, observed, client):
        _invoke(wt.shell_view_tool, description="d", session_id="a")
        _invoke(wt.shell_view_tool, description="d", session_id="b")
        assert {line["obs_id"] for line in observed} == {"shell:a", "shell:b"}


class TestObservationFailureNeverBreaksTheTool:
    """Logging is a nicety; a command must not fail because logging did.

    The tools rely on `_write_sandbox_observation` swallowing its own errors --
    they call it bare, so anything it raised would surface to the agent as a
    failed shell command. That contract is what these pin.
    """

    def test_the_writer_never_raises_when_the_log_cannot_be_written(self, monkeypatch, tmp_path):
        from deerflow.sandbox import tools as sandbox_tools

        monkeypatch.setattr(sandbox_tools, "_thread_id_for_observation", lambda sid: "t1")

        class _Paths:
            def thread_dir(self, thread_id, user_id=None):
                raise OSError("read-only file system")

        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: _Paths())

        # No exception, no return value -- it simply does nothing.
        assert sandbox_tools._write_sandbox_observation("sb", "shell_view", None, "x") is None

    def test_the_tool_still_returns_its_output_when_logging_is_impossible(self, monkeypatch, client):
        from deerflow.sandbox import tools as sandbox_tools

        monkeypatch.setattr(wt, "_get_sandbox_id", lambda runtime: "sb-1")
        monkeypatch.setattr(sandbox_tools, "_thread_id_for_observation", lambda sid: None)

        assert _invoke(wt.shell_view_tool, description="d", session_id="main") == "screen contents"


class TestShellViewOnlyRecordsRealChanges:
    """An agent polling a session must not fill the Terminal with identical rows.

    `shell_view` wrote an observation on every call, so a poll loop produced one
    row per poll for output that had not moved — noise that also evicts real
    events from the panel's 200-entry display window.
    """

    def test_repeated_views_of_an_unchanged_screen_write_once(self, observed, client):
        for _ in range(5):
            _invoke(wt.shell_view_tool, description="d", session_id="main")
        assert len(observed) == 1, "only the first view should have been recorded"

    def test_a_changed_screen_is_recorded(self, observed, client, monkeypatch):
        _invoke(wt.shell_view_tool, description="d", session_id="main")
        client.shell.view = lambda **kw: SimpleNamespace(data=SimpleNamespace(output="something new"))
        _invoke(wt.shell_view_tool, description="d", session_id="main")
        assert len(observed) == 2
        assert observed[-1]["replace"] == "something new"

    def test_the_tool_still_returns_the_screen_when_it_does_not_record(self, observed, client):
        first = _invoke(wt.shell_view_tool, description="d", session_id="main")
        second = _invoke(wt.shell_view_tool, description="d", session_id="main")
        assert first == second == "screen contents", "suppressing the log must not suppress the answer"

    def test_killing_a_session_forgets_its_screen(self, observed, client):
        _invoke(wt.shell_view_tool, description="d", session_id="main")
        _invoke(wt.shell_kill_tool, description="d", session_id="main")
        _invoke(wt.shell_view_tool, description="d", session_id="main")
        views = [line for line in observed if line["tool"] == "shell_view"]
        assert len(views) == 2, "a reused session id must start from a clean slate"

    def test_two_sessions_do_not_mask_each_other(self, observed, client):
        _invoke(wt.shell_view_tool, description="d", session_id="a")
        _invoke(wt.shell_view_tool, description="d", session_id="b")
        assert len(observed) == 2


class TestClosedIsAskedNotInferred:
    def test_the_base_contract_declares_closed(self):
        """Every backend answers `closed`, so callers never have to guess."""
        from deerflow.sandbox.sandbox import Sandbox

        assert isinstance(Sandbox.closed, property)

    def test_the_poller_stops_on_the_flag_not_on_a_message(self):
        """Pin the coupling that was removed: dev_server must not decide by
        searching an error string."""
        from pathlib import Path

        source = Path(wt.__file__).parent.parent.parent / "sandbox" / "dev_server.py"
        text = source.read_text()
        assert "sandbox.closed" in text, "the poller must ask the sandbox directly"
        # The code form, not any mention: the comment above it deliberately
        # quotes the old error text to explain what was removed.
        assert '"has no attribute" in content' not in text, "the string match is back"

    def test_a_released_aio_sandbox_reports_closed(self):
        from unittest.mock import patch

        with patch("deerflow.community.aio_sandbox.aio_sandbox.AioSandboxClient"):
            from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox

            sb = AioSandbox(id="t", base_url="http://localhost:8080")
            assert sb.closed is False
            sb.close()
            assert sb.closed is True, "the poller's stop condition depends on this"

    def test_using_a_released_sandbox_raises_a_typed_error(self):
        from unittest.mock import patch

        import pytest as _pytest

        from deerflow.sandbox.exceptions import SandboxError

        with patch("deerflow.community.aio_sandbox.aio_sandbox.AioSandboxClient"):
            from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox

            sb = AioSandbox(id="sb-9", base_url="http://localhost:8080")
            sb.close()
            with _pytest.raises(SandboxError, match="sb-9"):
                sb._require_client()
