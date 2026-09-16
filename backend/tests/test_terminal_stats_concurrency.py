"""The Terminal's "N cmds" counter must hold under concurrent commands.

`_write_sandbox_observation` incremented terminal_stats.json with a bare
read-modify-write and no lock::

    current = int(json.loads(stats_path.read_text() or "0"))
    stats_path.write_text(str(current + 1))

Nothing serialises that. Subagents share their parent thread's sandbox and run
on a thread pool, so several commands write the same counter at once -- the
Agent's Computer header routinely shows "4 running". Two threads both read 75
and both write 76, so the total silently drifts *low*, and the UI renders it
without the "~" prefix, i.e. as exact.

`write_text` also truncates in place, so `GET /api/sandbox/terminal-stats`
reading mid-write sees an empty file, raises, and returns `total_commands:
None` -- at which point the panel falls back to its approximate window count
and the number visibly changes for no reason the user can see.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

# `deerflow.sandbox.tools` and `deerflow.tools.builtins.workspace_tools` import
# each other, so importing the former *first* in a fresh interpreter raises
# ImportError. Entering from the workspace_tools side is the order the
# application itself uses (same note as tests/test_bash_streaming.py).
import deerflow.tools.builtins.workspace_tools  # noqa: F401,E402  isort:skip
from deerflow.sandbox import tools as sandbox_tools  # noqa: E402  isort:skip

COMMANDS = 200
READERS = 8


@pytest.fixture
def stats_dir(tmp_path, monkeypatch):
    """Point the observation writer at a throwaway thread directory."""
    log_path = tmp_path / "sandbox.log"
    monkeypatch.setattr(sandbox_tools, "_thread_id_for_observation", lambda _sid: "t-test")
    monkeypatch.setattr(sandbox_tools, "_sandbox_log_file", lambda _tid: log_path)
    # The WS emit is best-effort and irrelevant here; leave it alone so the
    # test exercises the real code path rather than a trimmed one.
    return tmp_path


def _read_total(stats_path: Path) -> int:
    """terminal_stats.json is {"total": N, "log_offset": B}."""
    return int(json.loads(stats_path.read_text())["total"])


def test_concurrent_commands_do_not_lose_increments(stats_dir):
    """N commands issued from N threads must count exactly N."""
    barrier = threading.Barrier(COMMANDS)

    def one(i: int) -> None:
        barrier.wait()  # maximise overlap on the read-modify-write
        sandbox_tools._write_sandbox_observation("local:t-test", "bash", None, f"$ cmd{i}")

    threads = [threading.Thread(target=one, args=(i,)) for i in range(COMMANDS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert _read_total(stats_dir / "terminal_stats.json") == COMMANDS


def test_counter_is_never_observed_torn(stats_dir):
    """A concurrent reader must never see a truncated/empty counter file."""
    stats_path = stats_dir / "terminal_stats.json"
    stop = threading.Event()
    torn: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                raw = stats_path.read_text()
            except OSError:
                continue
            if raw == "":
                torn.append(raw)
                return
            try:
                int(json.loads(raw)["total"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                torn.append(raw)
                return

    readers = [threading.Thread(target=reader) for _ in range(READERS)]
    for r in readers:
        r.start()
    try:
        for i in range(COMMANDS):
            sandbox_tools._write_sandbox_observation("local:t-test", "bash", None, f"$ cmd{i}")
    finally:
        stop.set()
        for r in readers:
            r.join()

    assert torn == [], f"reader observed a torn counter: {torn[:3]}"


def test_counter_stays_world_readable(stats_dir):
    """The counter must not silently become root-only.

    `atomic_write_text` writes through `NamedTemporaryFile`, which creates at
    0600. Switching to it for the torn-read fix therefore flipped
    terminal_stats.json from 0644 to 0600 -- invisible in the container (the
    gateway writes and reads it as the same user) but unreadable to every
    host-side reader. Pin the mode so the atomic write cannot smuggle a
    permissions change in again.
    """
    sandbox_tools._write_sandbox_observation("local:t-test", "bash", None, "$ cmd")
    mode = (stats_dir / "terminal_stats.json").stat().st_mode & 0o777
    assert mode == 0o644, f"expected 0644, got {mode:o}"
