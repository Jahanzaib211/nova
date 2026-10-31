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
