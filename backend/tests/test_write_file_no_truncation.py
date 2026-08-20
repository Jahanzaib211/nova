"""write_file integrity regression — pinned by the 2026-08-14 self-probe.

The self-probe sent a 102 810 B payload and observed 68 909 B landing on
disk with an "OK" success indicator (silent truncation). The original
``test_write_file_tool_size_guard.py`` covers the *guard* (auto-chunk,
hard reject, env override, UTF-8 boundaries) but not the **byte-exact
round-trip** that the probe broke. This file pins the round-trip contract
end-to-end against a real ``LocalSandbox`` against a temp dir.

All cases call ``local_sandbox.write_file`` directly to avoid the
pre-existing circular import between ``workspace_tools.py`` ↔ ``tools.py``.

If a future change to ``sandbox.write_file`` causes silent truncation,
the corresponding case here will fail and the next self-probe will catch it.

The tool layer's auto-chunk, size-guard, and byte-count message are
covered by ``test_write_file_tool_size_guard.py`` — this file is NOT
a duplicate of those.

Run with:

    export LD_LIBRARY_PATH=.../nvidia/cublas/lib:.../nvidia/cudnn/lib:$LD_LIBRARY_PATH
    cd backend && PYTHONPATH=. uv run pytest tests/test_write_file_no_truncation.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_THREAD_DIR = "users/default/threads/t-write/user-data"
_VIRTUAL_OUT = "/mnt/user-data/workspace/out.txt"


@pytest.fixture
def workspace(tmp_path):
    paths_module = sys.modules["deerflow.config.paths"]
    fake = type("P", (), {})()
    fake.thread_dir = lambda tid, user_id=None: tmp_path / f"users/{user_id or 'default'}/threads/{tid}/user-data"
    fake.workspace_dir = lambda tid, user_id=None: fake.thread_dir(tid, user_id) / "workspace"
    paths_module.get_paths = lambda: fake
    return tmp_path


@pytest.fixture
def local_sandbox(workspace):
    from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping

    ws = workspace / _THREAD_DIR / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    pm = PathMapping(
        container_path="/mnt/user-data/workspace",
        local_path=str(ws),
        read_only=False,
    )
    return LocalSandbox(id="local:t-write", path_mappings=[pm])


@pytest.fixture
def resolved_ws(workspace):
    return workspace / _THREAD_DIR / "workspace"


def test_write_file_round_trips_102810_bytes(local_sandbox, resolved_ws):
    """The exact payload size from the self-probe. If this case lands less
    than 102 810 B on disk, the F2 truncation finding is reproducible."""
    payload = "x" * 102_810
    local_sandbox.write_file(_VIRTUAL_OUT, payload)
    actual = resolved_ws.joinpath("out.txt").read_bytes()
    assert actual == payload.encode("utf-8"), f"truncation reproduced: sent {len(payload):,} B, got {len(actual):,} B"


def test_write_file_round_trips_just_under_auto_chunk_threshold(local_sandbox, resolved_ws):
    """199 999 B sits below the 200 KB soft threshold — single-shot write."""
    payload = "y" * 199_999
    local_sandbox.write_file(_VIRTUAL_OUT, payload)
    actual = resolved_ws.joinpath("out.txt").read_bytes()
    assert actual == payload.encode("utf-8")


def test_write_file_round_trips_just_over_auto_chunk_threshold(local_sandbox, resolved_ws):
    """200 001 B would force auto-chunk at the tool layer; the underlying
    write_file still sees the full content."""
    payload = "z" * 200_001
    local_sandbox.write_file(_VIRTUAL_OUT, payload)
    actual = resolved_ws.joinpath("out.txt").read_bytes()
    assert actual == payload.encode("utf-8")


def test_write_file_round_trips_mid_chunk(local_sandbox, resolved_ws):
    """250 KB payload — would be two chunks at the tool layer."""
    payload = "a" * 250_000
    local_sandbox.write_file(_VIRTUAL_OUT, payload)
    actual = resolved_ws.joinpath("out.txt").read_bytes()
    assert actual == payload.encode("utf-8")


def test_write_file_round_trips_two_chunks_with_utf8_boundary(local_sandbox, resolved_ws):
    """Force the chunk boundary to land inside a multi-byte UTF-8 sequence.
    ``_split_utf8_safe`` must back off to a real character boundary or
    readers see ``U+FFFD`` replacement characters."""
    chunk = "🌍"  # 4 UTF-8 bytes
    payload = chunk * 40_000 + "x" * 50_000  # 210 000 bytes total
    local_sandbox.write_file(_VIRTUAL_OUT, payload)
    actual = resolved_ws.joinpath("out.txt").read_bytes()
    assert actual == payload.encode("utf-8"), f"byte count mismatch: sent {len(payload):,} B, got {len(actual):,} B"
    text = actual.decode("utf-8")
    assert "\ufffd" not in text, "UTF-8 boundary broken — replacement char present"


def test_append_true_round_trips_existing_plus_new(local_sandbox, resolved_ws):
    """Append mode must produce the concatenation of existing + new content."""
    initial = "A" * 1_000
    appendage = "B" * 242  # the probe's exact append size
    local_sandbox.write_file(_VIRTUAL_OUT, initial)
    local_sandbox.write_file(_VIRTUAL_OUT, appendage, append=True)
    actual = resolved_ws.joinpath("out.txt").read_bytes()
    assert actual == (initial + appendage).encode("utf-8")


def test_write_file_preserves_emoji_only_payload(local_sandbox, resolved_ws):
    """All-emoji payload (pure 4-byte UTF-8) at probe-adjacent size."""
    payload = "🌍" * 25_700  # 102 800 bytes — just under the 102 810 probe size
    local_sandbox.write_file(_VIRTUAL_OUT, payload)
    actual = resolved_ws.joinpath("out.txt").read_bytes()
    assert actual == payload.encode("utf-8")
    text = actual.decode("utf-8")
    assert "\ufffd" not in text


def test_write_file_overwrite_replaces_full_content(local_sandbox, resolved_ws):
    """Second write must replace (not append to) the first."""
    local_sandbox.write_file(_VIRTUAL_OUT, "A" * 10_000)
    local_sandbox.write_file(_VIRTUAL_OUT, "B" * 100)
    actual = resolved_ws.joinpath("out.txt").read_bytes()
    assert actual == ("B" * 100).encode("utf-8")
    assert len(actual) == 100
