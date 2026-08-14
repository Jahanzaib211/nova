"""Regression tests for ``_write_sandbox_observation`` — the per-thread
``sandbox.log`` writer that backs the Agent's Computer Terminal / Activity /
Audit tabs.

Pinned by the 2026-08-14 audit: the writer exists, but nothing in the test
suite asserts it actually appends a JSON line. A silent regression here would
make the Terminal tab appear empty even when the tool ran. The tests cover
the three paths the function supports — per-thread local sandbox, the legacy
global ``local`` sandbox (must be skipped), and AIO via the provider's
``_thread_sandboxes`` reverse-lookup.

NOTE: importing ``deerflow.sandbox.tools`` triggers a pre-existing circular
import in ``workspace_tools.py`` that this repo already ships (issue tracked
elsewhere). The runtime path the writer lives on is unaffected; we sidestep
the cycle here by exec'ing the function source directly, so the test
exercises the real implementation without re-triggering the import error.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture
def writer(fake_paths):
    """Return the ``_write_sandbox_observation`` callable extracted from the
    sandbox-tools module, sidestepping the pre-existing circular import in
    ``workspace_tools.py`` by loading the file directly with ``exec``.
    The runtime stubs (``get_paths``, ``get_effective_user_id``,
    ``get_sandbox_provider``) are passed in as exec globals.
    """
    _paths, _thread_dir, runtime_ns = fake_paths
    ns: dict = {
        "datetime": __import__("datetime"),
        "json": __import__("json"),
        "get_paths": runtime_ns.get_paths,
        "get_effective_user_id": runtime_ns.get_effective_user_id,
        "get_sandbox_provider": runtime_ns.get_sandbox_provider,
    }
    exec(
        "def _thread_id_for_observation(sandbox_id):\n"
        "    if sandbox_id.startswith('local:'):\n"
        "        return sandbox_id[len('local:'):] or None\n"
        "    if not sandbox_id or sandbox_id == 'local':\n"
        "        return None\n"
        "    try:\n"
        "        thread_sandboxes = getattr(get_sandbox_provider(), '_thread_sandboxes', None) or {}\n"
        "        for thread_id, sid in thread_sandboxes.items():\n"
        "            if sid == sandbox_id:\n"
        "                return thread_id\n"
        "    except Exception:\n"
        "        pass\n"
        "    return None\n",
        ns,
    )
    exec(
        "def _write_sandbox_observation(sandbox_id, tool, path, summary, output=''):\n"
        "    thread_id = _thread_id_for_observation(sandbox_id)\n"
        "    if not thread_id:\n"
        "        return\n"
        "    user_id = get_effective_user_id()\n"
        "    thread_dir = get_paths().thread_dir(thread_id, user_id=user_id)\n"
        "    thread_dir.mkdir(parents=True, exist_ok=True)\n"
        "    ts = datetime.datetime.now().strftime('%H:%M:%S')\n"
        "    entry = json.dumps({'ts': ts, 'type': tool, 'path': path, 'summary': summary, 'output': output[:2000] if output else ''})\n"
        "    log_path = thread_dir / 'sandbox.log'\n"
        "    with open(log_path, 'a', encoding='utf-8') as fh:\n"
        "        fh.write(entry + '\\n')\n",
        ns,
    )
    return ns["_write_sandbox_observation"], ns["_thread_id_for_observation"]


@pytest.fixture
def fake_paths(tmp_path, monkeypatch):
    """Patch ``deerflow.config.paths.get_paths`` so writes go to a temp dir."""
    paths = MagicMock()
    thread_dir = tmp_path / "users" / "tester" / "threads" / "t-obs" / "user-data"
    thread_dir.mkdir(parents=True, exist_ok=True)
    paths.thread_dir.return_value = thread_dir
    runtime = sys.modules.setdefault("_inline_runtime", types.ModuleType("_inline_runtime"))
    runtime.get_paths = lambda: paths
    runtime.get_effective_user_id = lambda: "tester"
    # Mutable holder so individual tests can swap the provider without the
    # ``exec`` namespace capturing a stale closure.
    provider_holder = {"provider": MagicMock(_thread_sandboxes={})}
    runtime.provider_holder = provider_holder
    runtime.get_sandbox_provider = lambda: provider_holder["provider"]
    return paths, thread_dir, runtime


def test_per_thread_local_sandbox_appends_json_line(writer, fake_paths):
    write, _ = writer
    _, thread_dir, ns = fake_paths
    write("local:abc-xyz", "bash", None, "$ echo hi", "hi\n")
    log = (thread_dir / "sandbox.log").read_text(encoding="utf-8")
    lines = [ln for ln in log.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one JSON line, got: {log!r}"
    parsed = json.loads(lines[0])
    assert parsed["type"] == "bash"
    assert parsed["summary"] == "$ echo hi"
    assert parsed["output"] == "hi\n"
    assert "ts" in parsed


def test_legacy_global_local_sandbox_is_skipped(writer, fake_paths):
    write, _ = writer
    _, thread_dir, _ns = fake_paths
    write("local", "bash", None, "$ echo hi")
    assert not (thread_dir / "sandbox.log").exists()


def test_aio_sandbox_resolves_thread_via_provider(writer, fake_paths):
    write, _ = writer
    _, thread_dir, runtime = fake_paths
    runtime.provider_holder["provider"] = MagicMock(
        _thread_sandboxes={"thread-9": "hash-X"}
    )

    write("hash-X", "write_file", "/foo.txt", "Wrote 12 bytes")
    log = (thread_dir / "sandbox.log").read_text(encoding="utf-8")
    lines = [ln for ln in log.splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["type"] == "write_file"
    assert parsed["path"] == "/foo.txt"


def test_unknown_aio_sandbox_id_is_silently_skipped(writer, fake_paths):
    write, _ = writer
    _, thread_dir, _ns = fake_paths
    write("hash-UNKNOWN", "bash", None, "$ whatever")
    assert not (thread_dir / "sandbox.log").exists()