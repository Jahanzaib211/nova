"""Regression tests for ``_write_sandbox_observation`` — the per-thread
``sandbox.log`` writer that backs the Agent's Computer Terminal / Activity /
Audit tabs.

Pinned by the 2026-08-14 audit: the writer exists, but nothing in the test
suite asserts it actually appends a JSON line. A silent regression here would
make the Terminal tab appear empty even when the tool ran. The tests cover
the three paths the function supports — per-thread local sandbox, the legacy
global ``local`` sandbox (must be skipped), and AIO via the provider's
``_thread_sandboxes`` reverse-lookup.

**This file used to test a stub.** It ``exec()``d a hand-written
reimplementation of the writer — one that had no ``obs_id``/``state``/``delta``/
``replace`` parameters and never touched ``terminal_stats.json`` — on the
grounds that importing ``deerflow.sandbox.tools`` hit a circular import. So it
could pass while the shipped writer was broken, and it provided no cover at all
for the half of the function that maintains the command counter. That is the
same trap that let a wrong ``is_command_line`` ship green, and the same one that
hid the skill tool-policy bug.

The cycle is real but is an *ordering* problem, not a wall: importing
``deerflow.tools.builtins.workspace_tools`` first is the order the application
itself uses, and several sibling tests already do exactly that. So these tests
now drive the real function.
"""

from __future__ import annotations

import json

import pytest

# `deerflow.sandbox.tools` and `deerflow.tools.builtins.workspace_tools` import
# each other, so importing the former *first* in a fresh interpreter raises
# ImportError. Entering from the workspace_tools side is the order the
# application itself uses (same note as tests/test_terminal_stats_concurrency.py).
import deerflow.tools.builtins.workspace_tools  # noqa: F401,E402  isort:skip
from deerflow.sandbox import tools as sandbox_tools  # noqa: E402  isort:skip


@pytest.fixture
def thread_log(tmp_path, monkeypatch):
    """Route the writer at a throwaway thread directory.

    Only the path resolution is faked: the writer, the command predicate and the
    counter reconciliation are all the shipped code.
    """
    log_path = tmp_path / "sandbox.log"
    resolved: dict[str, str | None] = {"thread_id": "t-obs"}

    monkeypatch.setattr(
        sandbox_tools,
        "_thread_id_for_observation",
        lambda sid: resolved["thread_id"] if sid not in ("", "local", None) else None,
    )
    monkeypatch.setattr(sandbox_tools, "_sandbox_log_file", lambda _tid: log_path)
    return log_path, resolved


def _lines(log_path):
    if not log_path.exists():
        return []
    return [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_per_thread_local_sandbox_appends_json_line(thread_log):
    log_path, _ = thread_log
    sandbox_tools._write_sandbox_observation("local:abc-xyz", "bash", None, "$ echo hi", "hi\n")
    records = _lines(log_path)
    assert len(records) == 1, f"expected one JSON line, got {records!r}"
    assert records[0]["type"] == "bash"
    assert records[0]["summary"] == "$ echo hi"
    assert records[0]["output"] == "hi\n"
    assert "ts" in records[0]


def test_legacy_global_local_sandbox_is_skipped(thread_log):
    log_path, resolved = thread_log
    resolved["thread_id"] = None
    sandbox_tools._write_sandbox_observation("local", "bash", None, "$ echo hi")
    assert not log_path.exists()


def test_unknown_sandbox_id_is_silently_skipped(thread_log):
    log_path, resolved = thread_log
    resolved["thread_id"] = None
    sandbox_tools._write_sandbox_observation("hash-UNKNOWN", "bash", None, "$ whatever")
    assert not log_path.exists()


def test_file_tools_record_their_path(thread_log):
    log_path, _ = thread_log
    sandbox_tools._write_sandbox_observation("hash-X", "write_file", "/foo.txt", "Wrote 12 bytes")
    records = _lines(log_path)
    assert len(records) == 1
    assert records[0]["type"] == "write_file"
    assert records[0]["path"] == "/foo.txt"


class TestTheCounterHalfTheStubNeverCovered:
    """``_write_sandbox_observation`` also maintains ``terminal_stats.json``.

    The replaced stub had no idea this existed, so none of it was under test
    from this file.
    """

    def _total(self, log_path):
        stats = log_path.parent / "terminal_stats.json"
        return json.loads(stats.read_text())["total"]

    def test_a_command_moves_the_counter(self, thread_log):
        log_path, _ = thread_log
        sandbox_tools._write_sandbox_observation("local:t", "bash", None, "$ echo one")
        assert self._total(log_path) == 1

    def test_file_work_does_not_move_the_counter(self, thread_log):
        log_path, _ = thread_log
        sandbox_tools._write_sandbox_observation("local:t", "bash", None, "$ echo one")
        sandbox_tools._write_sandbox_observation("local:t", "read_file", "/a", "Read 3 chars")
        sandbox_tools._write_sandbox_observation("local:t", "write_file", "/a", "Wrote 3 bytes")
        assert self._total(log_path) == 1, "file work is not a command"

    def test_a_streamed_command_counts_once_not_once_per_frame(self, thread_log):
        log_path, _ = thread_log
        sandbox_tools._write_sandbox_observation("local:t", "bash", None, "$ npm i", obs_id="x1", state="running")
        for chunk in ("a\n", "b\n", "c\n"):
            sandbox_tools._write_sandbox_observation("local:t", "bash", None, "", obs_id="x1", state="running", delta=chunk)
        sandbox_tools._write_sandbox_observation("local:t", "bash", None, "$ npm i", "all", obs_id="x1", state="done")
        assert self._total(log_path) == 1

    def test_shell_session_counts_and_its_bookkeeping_does_not(self, thread_log):
        log_path, _ = thread_log
        sandbox_tools._write_sandbox_observation("local:t", "shell_session", "/w", "[main] top", obs_id="shell:main", state="running")
        sandbox_tools._write_sandbox_observation("local:t", "shell_view", None, "[main] view", obs_id="shell:main", state="running", replace="screen")
        sandbox_tools._write_sandbox_observation("local:t", "shell_kill", None, "[main] killed", obs_id="shell:main", state="done")
        assert self._total(log_path) == 1

    def test_the_counter_file_is_readable_by_non_root(self, thread_log):
        """atomic_write_text writes through NamedTemporaryFile, which is 0600."""
        log_path, _ = thread_log
        sandbox_tools._write_sandbox_observation("local:t", "bash", None, "$ echo one")
        mode = (log_path.parent / "terminal_stats.json").stat().st_mode & 0o777
        assert mode == 0o644, f"counter written {oct(mode)}, unreadable to the gateway's reader"
