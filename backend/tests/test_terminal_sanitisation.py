"""Raw terminal bytes must not reach the panel, and dev output must not be a command.

Two defects, one writer. ``_append_devlog_to_sandbox_log`` mirrored dev-server
output into ``sandbox.log`` by hand-rolling its own JSON line, which meant it

* tagged every line ``type: "bash"`` -- so dev-server chatter counted as executed
  commands. Measured across 89 live logs: **1,515 of 3,455 counted "commands"
  (44%) were dev-server noise**, and the worst thread was inflated 7.9x; and
* wrote ``summary`` raw. ``summary`` was also the one field the shared writer
  never sanitised or truncated, so ANSI cursor codes and runs of NUL bytes went
  straight into a ``<pre>`` and rendered as the garbled text users reported
  (39 ANSI-bearing lines and 3 NUL-bearing lines are on disk right now).

Colour is kept deliberately: stripping SGR too would make a dev server's red
error line indistinguishable from its ready line.
"""

from __future__ import annotations

import json

import pytest

import deerflow.tools.builtins.workspace_tools  # noqa: F401,E402  isort:skip
from deerflow.sandbox import tools as sandbox_tools  # noqa: E402  isort:skip
from deerflow.utils.sanitize import sanitize_terminal_text, strip_all_ansi  # noqa: E402  isort:skip

ESC = "\x1b"


class TestSanitizePolicy:
    def test_plain_text_is_untouched(self) -> None:
        assert sanitize_terminal_text("ready in 1.2s") == "ready in 1.2s"

    def test_colour_survives(self) -> None:
        coloured = f"{ESC}[32mready{ESC}[0m"
        assert sanitize_terminal_text(coloured) == coloured

    def test_cursor_codes_are_dropped(self) -> None:
        """The exact shape found in the live logs."""
        assert sanitize_terminal_text(f"[dev] {ESC}[?25h") == "[dev] "

    def test_nul_runs_are_dropped(self) -> None:
        assert sanitize_terminal_text("[dev] " + "\x00" * 20 + " GET / 200") == "[dev]  GET / 200"

    def test_newlines_and_tabs_survive(self) -> None:
        assert sanitize_terminal_text("a\nb\tc") == "a\nb\tc"

    def test_crlf_keeps_the_break(self) -> None:
        assert sanitize_terminal_text("a\r\nb") == "a\nb"

    def test_lone_carriage_return_is_dropped(self) -> None:
        """A \\r progress bar has no overwrite semantics in a <pre>."""
        assert sanitize_terminal_text("50%\r100%") == "50%100%"

    def test_is_idempotent(self) -> None:
        once = sanitize_terminal_text(f"{ESC}[31ma{ESC}[0m\x00")
        assert sanitize_terminal_text(once) == once

    def test_strip_all_ansi_drops_colour_too(self) -> None:
        assert strip_all_ansi(f"{ESC}[32mready{ESC}[0m") == "ready"


@pytest.fixture
def thread_log(tmp_path, monkeypatch):
    log_path = tmp_path / "sandbox.log"
    monkeypatch.setattr(sandbox_tools, "_thread_id_for_observation", lambda _sid: "t-dev")
    monkeypatch.setattr(sandbox_tools, "_sandbox_log_file", lambda _tid: log_path)
    return log_path


def _records(log_path):
    return [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestTheWriterSanitises:
    def test_summary_is_sanitised(self) -> None:
        """summary was the only field with neither sanitisation nor truncation."""
        # Exercised through the real writer below; this pins the field explicitly.
        assert "\x00" not in sanitize_terminal_text("x" + "\x00" * 5)

    def test_every_field_is_cleaned(self, thread_log) -> None:
        dirty = f"out{ESC}[?25h\x00"
        sandbox_tools._write_sandbox_observation("local:t", "bash", None, f"$ cmd{ESC}[?25h\x00", dirty)
        rec = _records(thread_log)[0]
        assert "\x00" not in rec["summary"] and f"{ESC}[?25h" not in rec["summary"]
        assert "\x00" not in rec["output"] and f"{ESC}[?25h" not in rec["output"]

    def test_delta_and_replace_are_cleaned(self, thread_log) -> None:
        sandbox_tools._write_sandbox_observation("local:t", "bash", None, "", obs_id="x", state="running", delta=f"a{ESC}[2Kb")
        sandbox_tools._write_sandbox_observation("local:t", "bash", None, "", obs_id="x", state="running", replace=f"c\x00d")
        recs = _records(thread_log)
        assert recs[0]["delta"] == "ab"
        assert recs[1]["replace"] == "cd"

    def test_colour_reaches_the_panel(self, thread_log) -> None:
        sandbox_tools._write_sandbox_observation("local:t", "bash", None, "$ x", f"{ESC}[32mok{ESC}[0m")
        assert f"{ESC}[32m" in _records(thread_log)[0]["output"]


class TestDevServerIsNotACommand:
    def test_dev_server_type_is_not_counted(self) -> None:
        assert sandbox_tools.is_command_line({"type": "dev_server", "summary": "[dev] ready"}) is False

    def test_legacy_dev_lines_are_not_counted(self) -> None:
        """Already on disk as type "bash" -- the log is replayed on read, so the
        prefix rule is what repairs existing threads."""
        assert sandbox_tools.is_command_line({"type": "bash", "summary": "[dev] GET / 200 in 28ms"}) is False

    def test_a_real_command_still_counts(self) -> None:
        assert sandbox_tools.is_command_line({"type": "bash", "summary": "$ npm run dev"}) is True

    def test_a_command_merely_mentioning_dev_still_counts(self) -> None:
        assert sandbox_tools.is_command_line({"type": "bash", "summary": "$ echo '[dev] hi'"}) is True

    def test_the_mirror_writes_dev_server_not_bash(self, thread_log, monkeypatch) -> None:
        """The retag itself: the mirror must go through the shared writer."""
        import deerflow.sandbox.dev_server as ds

        monkeypatch.setattr(ds, "_sandbox_id_for_thread", lambda _tid: "local:t")
        ds._append_devlog_to_sandbox_log("t-dev", f"ready{ESC}[?25h\x00")
        rec = _records(thread_log)[0]
        assert rec["type"] == "dev_server", "dev output tagged as a shell command again"
        assert rec["summary"].startswith("[dev] ")
        assert "\x00" not in rec["summary"]
        assert sandbox_tools.is_command_line(rec) is False

    def test_dev_output_does_not_move_the_counter(self, thread_log, monkeypatch) -> None:
        import deerflow.sandbox.dev_server as ds

        monkeypatch.setattr(ds, "_sandbox_id_for_thread", lambda _tid: "local:t")
        sandbox_tools._write_sandbox_observation("local:t", "bash", None, "$ npm run dev")
        for _ in range(25):
            ds._append_devlog_to_sandbox_log("t-dev", "GET / 200 in 28ms")
        total = json.loads((thread_log.parent / "terminal_stats.json").read_text())["total"]
        assert total == 1, f"25 dev-server lines inflated the count to {total}"
