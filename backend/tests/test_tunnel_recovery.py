"""Tests for the Cloudflare Tunnel auto-recovery path (P12_tunnel).

Verifies:
- fix_tunnel calls ``systemctl reset-failed`` BEFORE restart (so
  start-limit-hit doesn't silently block recovery).
- fix_tunnel only returns True after verifying the systemd unit is
  active (so the next cycle's probe sees the actual state).
- All subprocess invocations go through ``sudo -n`` (no interactive
  password prompt).
- Failures at any step return False (the next probe cycle retries).

These tests exercise the real ``subprocess.run`` calls against a
mocked ``subprocess.run`` so they run without sudo or root.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# healthcheck-daemon.py is a top-level script (not a package). Load it
# via importlib and register it in sys.modules so dataclasses can resolve
# their owning class via cls.__module__.
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "healthcheck-daemon.py"
_spec = importlib.util.spec_from_file_location("healthcheck_daemon", _SCRIPT)
hd = importlib.util.module_from_spec(_spec)
sys.modules["healthcheck_daemon"] = hd
_spec.loader.exec_module(hd)


def _completed_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def test_fix_tunnel_resets_failed_before_restart():
    """The preflight reset-failed must be issued before the restart.
    Without it, restart silently fails on a start-limit-hit unit."""
    call_log: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        call_log.append(list(cmd))
        # sudo -n -l returns a sudoers dump that includes reset-failed.
        if cmd[:3] == ["sudo", "-n", "-l"]:
            return _completed_proc(
                0,
                stdout=(
                    "User jahanzaib may run the following commands:\n"
                    "    (root) NOPASSWD: /usr/bin/systemctl reset-failed "
                    "cloudflared-nova.service\n"
                ),
            )
        if cmd[:3] == ["sudo", "-n", "systemctl"] and cmd[3] == "reset-failed":
            return _completed_proc(0)
        if cmd[:3] == ["sudo", "-n", "systemctl"] and cmd[3] == "restart":
            return _completed_proc(0)
        if cmd[:3] == ["sudo", "-n", "systemctl"] and cmd[3] == "is-active":
            return _completed_proc(0, stdout="active")
        return _completed_proc(0)

    with patch.object(hd.subprocess, "run", side_effect=fake_run):
        with patch.object(hd.time, "sleep"):
            result = hd.fix_tunnel()

    assert result is True
    # Step 0 = sudoers check, Step 1 = reset-failed, Step 2 = restart,
    # Step 3+ = is-active polling.
    assert call_log[0][:2] == ["sudo", "-n"], f"first call should be sudo -l, got {call_log[0]}"
    assert call_log[1][:4] == ["sudo", "-n", "systemctl", "reset-failed"]
    assert call_log[2][:4] == ["sudo", "-n", "systemctl", "restart"]
    # The is-active polling must happen AFTER the restart.
    assert any(call[:4] == ["sudo", "-n", "systemctl", "is-active"] for call in call_log)


def test_fix_tunnel_returns_false_when_restart_fails():
    """If restart exits non-zero, fix_tunnel returns False so the next
    cycle retries (instead of falsely claiming a fix)."""
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["sudo", "-n", "-l"]:
            return _completed_proc(0, stdout="(root) NOPASSWD: reset-failed")
        if cmd[3] == "reset-failed":
            return _completed_proc(0)
        if cmd[3] == "restart":
            return _completed_proc(1, stderr="Failed to restart")
        return _completed_proc(0)

    with patch.object(hd.subprocess, "run", side_effect=fake_run):
        result = hd.fix_tunnel()

    assert result is False


def test_fix_tunnel_returns_false_when_post_restart_verification_times_out():
    """If the systemd unit doesn't come up within 10s (e.g. cloudflared
    immediately crashes again), fix_tunnel returns False so the next
    cycle retries — and the dashboard stays RED."""
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["sudo", "-n", "-l"]:
            return _completed_proc(0, stdout="(root) NOPASSWD: reset-failed")
        if cmd[3] == "reset-failed":
            return _completed_proc(0)
        if cmd[3] == "restart":
            return _completed_proc(0)
        if cmd[3] == "is-active":
            # Always returns inactive
            return _completed_proc(1, stdout="inactive")
        return _completed_proc(0)

    # Avoid the 10s real-time wait.
    class _InstantMonotonic:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    clock = _InstantMonotonic()
    with patch.object(hd.subprocess, "run", side_effect=fake_run):
        with patch.object(hd.time, "monotonic", clock):
            with patch.object(hd.time, "sleep"):
                # Advance the clock past the deadline so the polling loop exits.
                def fake_sleep(_seconds):
                    clock.now += 100.0
                with patch.object(hd.time, "sleep", fake_sleep):
                    result = hd.fix_tunnel()
    assert result is False


def test_fix_tunnel_treats_reset_failed_nonzero_as_nonfatal():
    """If reset-failed returns non-zero (e.g. unit not in failed state),
    fix_tunnel must continue with the restart rather than abort early."""
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["sudo", "-n", "-l"]:
            return _completed_proc(0, stdout="(root) NOPASSWD: reset-failed")
        if cmd[3] == "reset-failed":
            return _completed_proc(1, stderr="Unit not loaded")
        if cmd[3] == "restart":
            return _completed_proc(0)
        if cmd[3] == "is-active":
            return _completed_proc(0, stdout="active")
        return _completed_proc(0)

    with patch.object(hd.subprocess, "run", side_effect=fake_run):
        with patch.object(hd.time, "sleep"):
            result = hd.fix_tunnel()
    assert result is True


def test_fix_tunnel_warns_when_sudoers_missing_reset_failed(caplog):
    """When the sudoers entry for reset-failed is missing, fix_tunnel
    must emit a clear warning so the operator can apply the install
    script. Without this, start-limit-hit outages silently fail."""
    def fake_run(cmd, **kwargs):
        # sudo -n -l returns the user's NOPASSWD list — omit reset-failed.
        if cmd[:2] == ["sudo", "-n"] and cmd[2] == "-l":
            return _completed_proc(
                0,
                stdout=(
                    "User jahanzaib may run the following commands:\n"
                    "    (root) NOPASSWD: /usr/bin/systemctl restart "
                    "cloudflared-nova.service\n"
                    "    (root) NOPASSWD: /usr/bin/systemctl start "
                    "cloudflared-nova.service\n"
                ),
            )
        if cmd[3] == "reset-failed":
            return _completed_proc(0)
        if cmd[3] == "restart":
            return _completed_proc(0)
        if cmd[3] == "is-active":
            return _completed_proc(0, stdout="active")
        return _completed_proc(0)

    with patch.object(hd.subprocess, "run", side_effect=fake_run):
        with patch.object(hd.time, "sleep"):
            import logging

            caplog.set_level(logging.WARNING, logger="healthcheck_daemon")
            result = hd.fix_tunnel()

    assert result is True
    assert any(
        "reset-failed cloudflared-nova" in rec.message
        and "next start-limit-hit" in rec.message
        for rec in caplog.records
    ), caplog.records
