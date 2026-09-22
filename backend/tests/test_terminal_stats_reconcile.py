"""The command total is derived from sandbox.log, so it can repair itself.

The counter used to be an incrementally-maintained integer. An integer is only
ever as correct as every write that touched it, and the unlocked version that
shipped lost 32% of one real thread's commands (216 recorded as 146) -- damage
no amount of later locking undoes. Deriving the total from the log instead
makes a wrong value converge on the next call.
"""

from __future__ import annotations

import json

import pytest

# `deerflow.sandbox.tools` and `deerflow.tools.builtins.workspace_tools` import
# each other; enter from the workspace_tools side, as the application does.
import deerflow.tools.builtins.workspace_tools  # noqa: F401,E402  isort:skip
from deerflow.sandbox.tools import reconcile_terminal_stats  # noqa: E402  isort:skip


def _cmd(i: int) -> str:
    return json.dumps({"ts": "00:00:00", "type": "bash", "summary": f"$ cmd{i}"})


def _chunk(i: int) -> str:
    """A streamed delta frame -- must never count."""
    return json.dumps({"ts": "00:00:00", "type": "bash", "id": f"x{i}", "state": "running", "delta": "out"})


@pytest.fixture
def log(tmp_path):
    return tmp_path / "sandbox.log"


def _state(log):
    return json.loads((log.parent / "terminal_stats.json").read_text())


def test_counts_commands_from_an_existing_log(log):
    log.write_text("\n".join(_cmd(i) for i in range(7)) + "\n")
    assert reconcile_terminal_stats(log) == 7


def test_ignores_streamed_chunks(log):
    lines = [_cmd(0)] + [_chunk(0) for _ in range(50)]
    log.write_text("\n".join(lines) + "\n")
    assert reconcile_terminal_stats(log) == 1


def test_is_incremental_and_idempotent(log):
    log.write_text("\n".join(_cmd(i) for i in range(3)) + "\n")
    assert reconcile_terminal_stats(log) == 3
    # Nothing new: repeated calls must not double-count.
    assert reconcile_terminal_stats(log) == 3
    assert reconcile_terminal_stats(log) == 3
    with log.open("a") as fh:
        fh.write(_cmd(99) + "\n")
    assert reconcile_terminal_stats(log) == 4


def test_repairs_a_stale_legacy_counter(log):
    """A bare int is the damaged shape -- discard it and rescan.

    This is what heals threads that ran under the unlocked increment.
    """
    log.write_text("\n".join(_cmd(i) for i in range(216)) + "\n")
    (log.parent / "terminal_stats.json").write_text("146")  # the lossy value
    assert reconcile_terminal_stats(log) == 216


def test_ignores_a_partial_trailing_line(log):
    """A read can land mid-append; a torn line must not be consumed."""
    log.write_text(_cmd(0) + "\n" + _cmd(1) + "\n" + '{"ts": "00:00:00", "type": "ba')
    assert reconcile_terminal_stats(log) == 2
    # Once the writer completes that line, it counts -- and only once.
    with log.open("a") as fh:
        fh.write('sh", "summary": "$ x"}\n')
    assert reconcile_terminal_stats(log) == 3


def test_recounts_when_the_log_is_truncated(log):
    log.write_text("\n".join(_cmd(i) for i in range(10)) + "\n")
    assert reconcile_terminal_stats(log) == 10
    # Rotated/truncated: the stored offset now points past EOF.
    log.write_text(_cmd(0) + "\n")
    assert reconcile_terminal_stats(log) == 1


def test_survives_a_corrupt_state_file(log):
    log.write_text(_cmd(0) + "\n")
    (log.parent / "terminal_stats.json").write_text("{not json")
    assert reconcile_terminal_stats(log) == 1


def test_skips_unparseable_log_lines(log):
    log.write_text(_cmd(0) + "\nnot json at all\n" + _cmd(1) + "\n")
    assert reconcile_terminal_stats(log) == 2


def test_returns_none_without_a_log(tmp_path):
    assert reconcile_terminal_stats(tmp_path / "absent.log") is None


def test_persists_offset_so_steady_state_is_cheap(log):
    log.write_text("\n".join(_cmd(i) for i in range(4)) + "\n")
    reconcile_terminal_stats(log)
    st = _state(log)
    assert st["total"] == 4
    assert st["log_offset"] == log.stat().st_size
