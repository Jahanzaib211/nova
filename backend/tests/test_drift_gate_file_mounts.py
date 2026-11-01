"""drift-gate's file_mounts check: a container reading a replaced file's old
inode must be reported, not silently tolerated (stale config.yaml, 2026-10-30)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "drift-gate.py"


@pytest.fixture
def dg():
    spec = importlib.util.spec_from_file_location("drift_gate_fm", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["drift_gate_fm"] = mod
    spec.loader.exec_module(mod)
    return mod


def _wire(monkeypatch, dg, host_file: Path, container_bytes: bytes):
    mounts = [{"Type": "bind", "Source": str(host_file), "Destination": "/app/config.yaml"}, {"Type": "volume", "Source": "/var/lib/x", "Destination": "/data"}]
    monkeypatch.setattr(dg, "FILE_MOUNT_CONTAINERS", ("deer-flow-gateway",))
    monkeypatch.setattr(dg, "_run", lambda cmd, timeout=60: (0, json.dumps(mounts), ""))
    monkeypatch.setattr(dg.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, container_bytes, b""))


def test_matching_mount_is_green(monkeypatch, dg, tmp_path):
    f = tmp_path / "config.yaml"
    f.write_bytes(b"a: 1\n")
    _wire(monkeypatch, dg, f, b"a: 1\n")
    assert dg.check_file_mounts()["status"] == dg.GREEN


def test_stale_mount_is_red_and_named(monkeypatch, dg, tmp_path):
    f = tmp_path / "config.yaml"
    f.write_bytes(b"a: 2\n")
    _wire(monkeypatch, dg, f, b"a: 1\n")
    result = dg.check_file_mounts()
    assert result["status"] == dg.RED
    assert result["stale"] == ["deer-flow-gateway:/app/config.yaml"]


def test_nothing_comparable_is_yellow(monkeypatch, dg):
    monkeypatch.setattr(dg, "FILE_MOUNT_CONTAINERS", ("deer-flow-gateway",))
    monkeypatch.setattr(dg, "_run", lambda cmd, timeout=60: (1, "", "no such container"))
    assert dg.check_file_mounts()["status"] == dg.YELLOW


def _wire_claude_json(monkeypatch, dg, host_file: Path, container_bytes: bytes, extra: list[dict] | None = None):
    mounts = [{"Type": "bind", "Source": str(host_file), "Destination": "/root/.claude.json"}, *(extra or [])]
    monkeypatch.setattr(dg, "FILE_MOUNT_CONTAINERS", ("deer-flow-gateway",))
    monkeypatch.setattr(dg, "_run", lambda cmd, timeout=60: (0, json.dumps(mounts), ""))

    def fake_run(cmd, **kw):
        body = container_bytes if cmd[-1] == "/root/.claude.json" else b"a: 1\n"
        return subprocess.CompletedProcess(cmd, 0, body, b"")

    monkeypatch.setattr(dg.subprocess, "run", fake_run)


def test_stale_claude_json_is_reported_yellow_not_hidden(monkeypatch, dg, tmp_path):
    """The CLI rewrites .claude.json by rename, so a single-file mount of it
    goes stale by design. Skipping it hid that the container's copy (OAuth
    state) was old; reporting it red would pin the gate red forever."""
    f = tmp_path / ".claude.json"
    f.write_bytes(b'{"new": true}')
    _wire_claude_json(monkeypatch, dg, f, b'{"old": true}')
    result = dg.check_file_mounts()
    assert result["status"] == dg.YELLOW
    assert result["stale_by_design"] == ["deer-flow-gateway:/root/.claude.json"]
    assert "stale by design" in result["detail"]
    assert "mount the directory" in result["detail"]


def test_fresh_claude_json_is_green(monkeypatch, dg, tmp_path):
    f = tmp_path / ".claude.json"
    f.write_bytes(b'{"same": true}')
    _wire_claude_json(monkeypatch, dg, f, b'{"same": true}')
    assert dg.check_file_mounts()["status"] == dg.GREEN


def test_stale_claude_json_never_hides_a_real_stale_config(monkeypatch, dg, tmp_path):
    f = tmp_path / ".claude.json"
    f.write_bytes(b'{"new": true}')
    cfg = tmp_path / "config.yaml"
    cfg.write_bytes(b"a: 2\n")
    _wire_claude_json(monkeypatch, dg, f, b'{"old": true}', extra=[{"Type": "bind", "Source": str(cfg), "Destination": "/app/config.yaml"}])
    result = dg.check_file_mounts()
    assert result["status"] == dg.RED
    assert result["stale"] == ["deer-flow-gateway:/app/config.yaml"]
    assert result["stale_by_design"] == ["deer-flow-gateway:/root/.claude.json"]
