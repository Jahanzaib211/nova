"""Unit tests for scripts/healthcheck-daemon.py.

Run from repo root:
    cd backend && PYTHONPATH=../scripts uv run pytest tests/test_healthcheck_daemon.py -v

Probes that hit the network (probe_nginx, probe_llama_loopback, etc.) are
covered by integration tests against the live stack — these unit tests cover
the dispatch logic, status transitions, and the moat-clear fix.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

# Load the watchdog as a module (it lives outside the backend/ tree)
SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "healthcheck-daemon.py"
spec = importlib.util.spec_from_file_location("healthcheck_daemon", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
healthcheck = importlib.util.module_from_spec(spec)
sys.modules["healthcheck_daemon"] = healthcheck
spec.loader.exec_module(healthcheck)


# ---------------------------------------------------------------------------
# Status + ProbeResult dataclass
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_values(self):
        assert healthcheck.Status.GREEN.value == "green"
        assert healthcheck.Status.YELLOW.value == "yellow"
        assert healthcheck.Status.RED.value == "red"

    def test_probe_result_defaults(self):
        r = healthcheck.ProbeResult(name="P1", status=healthcheck.Status.GREEN)
        assert r.name == "P1"
        assert r.detail == ""
        assert r.latency_ms == 0.0
        assert r.fixed is False

    def test_probe_result_to_dict(self):
        r = healthcheck.ProbeResult(name="P1", status=healthcheck.Status.GREEN, detail="OK", latency_ms=12.5)
        d = r.to_dict()
        assert d == {"name": "P1", "status": healthcheck.Status.GREEN, "detail": "OK", "latency_ms": 12.5, "fixed": False}


# ---------------------------------------------------------------------------
# CycleReport aggregation
# ---------------------------------------------------------------------------


class TestCycleReport:
    def test_empty_cycle_is_green(self):
        report = healthcheck.CycleReport(cycle_id=1, started_at=0.0, duration_ms=0.0)
        assert report.overall == healthcheck.Status.GREEN
        assert report.exit_code == 0

    def test_single_red_flips_overall(self):
        report = healthcheck.CycleReport(cycle_id=1, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult("a", healthcheck.Status.GREEN))
        report.add(healthcheck.ProbeResult("b", healthcheck.Status.RED))
        assert report.overall == healthcheck.Status.RED

    def test_yellow_does_not_override_green(self):
        report = healthcheck.CycleReport(cycle_id=1, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult("a", healthcheck.Status.GREEN))
        report.add(healthcheck.ProbeResult("b", healthcheck.Status.YELLOW))
        assert report.overall == healthcheck.Status.YELLOW

    def test_yellow_does_not_override_red(self):
        report = healthcheck.CycleReport(cycle_id=1, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult("a", healthcheck.Status.RED))
        report.add(healthcheck.ProbeResult("b", healthcheck.Status.YELLOW))
        assert report.overall == healthcheck.Status.RED

    def test_exit_code_red_is_1(self):
        report = healthcheck.CycleReport(cycle_id=1, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult("a", healthcheck.Status.RED))
        assert report.exit_code == 1

    def test_exit_code_yellow_is_0(self):
        report = healthcheck.CycleReport(cycle_id=1, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult("a", healthcheck.Status.YELLOW))
        # default exit_code is 0 (yellow is masked, not failed)
        assert report.exit_code == 0

    def test_to_dict_roundtrip_keys(self):
        report = healthcheck.CycleReport(cycle_id=7, started_at=1234.5, duration_ms=99.0)
        report.add(healthcheck.ProbeResult("P1", healthcheck.Status.GREEN, "OK", 12.3))
        d = report.to_dict()
        assert d["cycle_id"] == 7
        assert d["started_at"] == 1234.5
        assert d["duration_ms"] == 99.0
        assert d["overall"] == "green"
        assert d["exit_code"] == 0
        assert len(d["probes"]) == 1
        assert d["probes"][0]["name"] == "P1"


# ---------------------------------------------------------------------------
# WatchdogState + dispatch_fixes
# ---------------------------------------------------------------------------


class TestDispatchFixes:
    def test_no_fixes_when_all_green(self):
        state = healthcheck.WatchdogState()
        report = healthcheck.CycleReport(cycle_id=1, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult("P1", healthcheck.Status.GREEN))
        healthcheck.dispatch_fixes(report, state)
        assert not any(p.fixed for p in report.probes)
        # No probe needs fixing; all consecutive counters should be 0 or absent
        assert all(v == 0 for v in state.consecutive_red.values())

    def test_single_red_does_not_trigger_fix(self):
        # First RED is treated as transient — fix only kicks in after 2 consecutive.
        state = healthcheck.WatchdogState()
        report = healthcheck.CycleReport(cycle_id=1, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult("P8_attestation", healthcheck.Status.RED, "drift"))
        healthcheck.dispatch_fixes(report, state)
        assert not any(p.fixed for p in report.probes)
        assert state.consecutive_red["P8_attestation"] == 1

    def test_two_consecutive_reds_triggers_attestation_fix(self, tmp_path, monkeypatch):
        # Patch fix_attestation to use a fake moat under tmp_path, then verify
        # the moat was cleared after two consecutive RED cycles.
        import shutil as _shutil

        moat = tmp_path / "ali-ram-moat"
        moat.mkdir()
        (moat / "golden_binary.hash").write_text("fake_sealed_hash")

        def patched_fix():
            healthcheck.log.warning("auto-fix: clearing test moat")
            _shutil.rmtree(moat, ignore_errors=True)
            return True

        monkeypatch.setattr(healthcheck, "fix_attestation", patched_fix)

        # Run two cycles of RED for P8_attestation
        state = healthcheck.WatchdogState()
        for cycle in (1, 2):
            report = healthcheck.CycleReport(cycle_id=cycle, started_at=0.0, duration_ms=0.0)
            report.add(healthcheck.ProbeResult("P8_attestation", healthcheck.Status.RED, "drift"))
            healthcheck.dispatch_fixes(report, state)

        assert not moat.exists(), "moat should have been cleared by the auto-fix"

    def test_yellow_resets_red_counter(self):
        state = healthcheck.WatchdogState()
        state.consecutive_red["P8_attestation"] = 1
        report = healthcheck.CycleReport(cycle_id=2, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult("P8_attestation", healthcheck.Status.YELLOW))
        healthcheck.dispatch_fixes(report, state)
        assert state.consecutive_red.get("P8_attestation", 0) == 0
        assert state.consecutive_yellow.get("P8_attestation", 0) == 1


# ---------------------------------------------------------------------------
# fix_attestation behavior (no real systemd needed)
# ---------------------------------------------------------------------------


class TestFixAttestation:
    def test_clears_existing_moat(self, tmp_path, monkeypatch):
        # Redirect /tmp/ali-ram-moat to tmp_path/ali-ram-moat by patching shutil.rmtree
        moat = tmp_path / "ali-ram-moat"
        moat.mkdir()
        (moat / "golden_binary.hash").write_text("test")

        captured = {"called": False}

        def fake_rmtree(path, *args, **kwargs):
            if str(path).endswith("ali-ram-moat"):
                captured["called"] = True
                return None
            return None

        monkeypatch.setattr(healthcheck.shutil, "rmtree", fake_rmtree)

        result = healthcheck.fix_attestation()
        assert result is True
        assert captured["called"], "shutil.rmtree should have targeted ali-ram-moat"


# ---------------------------------------------------------------------------
# run_cycle smoke (run with everything mocked — no network/docker)
# ---------------------------------------------------------------------------


class TestRunCycleSmoke:
    @pytest.mark.asyncio
    async def test_run_cycle_with_all_probes_mocked(self, monkeypatch):
        async def fake_green(*args, **kwargs):
            return healthcheck.ProbeResult("mock", healthcheck.Status.GREEN, "mock-ok", 1.0)

        # Patch every probe to return green
        for name in [
            "probe_nginx",
            "probe_gateway",
            "probe_frontend",
            "probe_ali_kernel",
            "probe_llama_loopback",
            "probe_llama_vram",
            "probe_containers",
            "probe_attestation",
        ]:
            monkeypatch.setattr(healthcheck, name, fake_green)

        state = healthcheck.WatchdogState()
        report = await healthcheck.run_cycle(state)
        assert report.overall == healthcheck.Status.GREEN
        assert len(report.probes) == 8
        assert all(p.status == healthcheck.Status.GREEN for p in report.probes)

    @pytest.mark.asyncio
    async def test_run_cycle_survives_single_probe_exception(self, monkeypatch):
        """A single probe raising must not kill the whole cycle — the JSON
        status line should still emit, the other 7 probes should be GREEN,
        and the failed probe should appear as a labeled RED."""

        async def fake_green(*args, **kwargs):
            return healthcheck.ProbeResult("mock", healthcheck.Status.GREEN, "mock-ok", 1.0)

        async def fake_boom(*args, **kwargs):
            raise RuntimeError("simulated probe failure")

        for name in [
            "probe_nginx",
            "probe_gateway",
            "probe_frontend",
            "probe_ali_kernel",
            "probe_llama_loopback",
            "probe_llama_vram",
            "probe_containers",
        ]:
            monkeypatch.setattr(healthcheck, name, fake_green)
        monkeypatch.setattr(healthcheck, "probe_attestation", fake_boom)

        state = healthcheck.WatchdogState()
        report = await healthcheck.run_cycle(state)

        # The attestation probe should appear as RED with its real name (P8_attestation),
        # not some auto-generated placeholder. This is the regression that
        # motivated the (name, coroutine) pair refactor.
        by_name = {p.name: p for p in report.probes}
        assert "P8_attestation" in by_name
        assert by_name["P8_attestation"].status == healthcheck.Status.RED
        assert "RuntimeError" in by_name["P8_attestation"].detail
        # The other 7 should still be GREEN and the cycle should still report.
        assert report.overall == healthcheck.Status.RED
        assert report.exit_code == 1
        green_count = sum(1 for p in report.probes if p.status == healthcheck.Status.GREEN)
        assert green_count == 7


class TestLogRouting:
    def test_logs_go_to_stderr(self):
        """Logs must route to stderr so they don't pollute the JSON status line
        on stdout — PM2 splits stdout/stderr into out_file vs error_file.

        pytest replaces log handlers with capture hooks, so we can't inspect
        the live handler stream. Instead, re-import the module in a subprocess
        with stderr captured and verify the log line landed in stderr (not
        stdout). That proves logging.basicConfig(stream=sys.stderr) was honored.
        """
        import subprocess

        script_path = str(SCRIPT_PATH)
        code = (
            "import sys, importlib.util;"
            "spec = importlib.util.spec_from_file_location('hc', "
            f"{script_path!r});"
            "m = importlib.util.module_from_spec(spec); "
            "sys.modules['hc'] = m; "  # @dataclass needs the module in sys.modules
            "spec.loader.exec_module(m);"
            "m.log.warning('test_log_routing_marker')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd="/home/jahanzaib/Desktop/nova",
        )
        assert "test_log_routing_marker" in result.stderr, (
            f"expected marker in stderr, got stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "test_log_routing_marker" not in result.stdout, (
            f"marker leaked to stdout: {result.stdout!r}"
        )

    def test_httpx_logging_silenced(self):
        import logging as _logging

        httpx_logger = _logging.getLogger("httpx")
        httpcore_logger = _logging.getLogger("httpcore")
        assert httpx_logger.level >= _logging.WARNING, "httpx logger must be silenced to WARNING+"
        assert httpcore_logger.level >= _logging.WARNING, "httpcore logger must be silenced to WARNING+"


class TestCycleDeadline:
    """Self-watchdog: if a probe hangs, the cycle's asyncio.wait_for fires
    and main_loop returns 1 so PM2 can restart us."""

    @pytest.mark.asyncio
    async def test_hung_probe_triggers_deadline(self, monkeypatch):
        """A probe that hangs longer than CYCLE_DEADLINE_SEC must be cut off
        by asyncio.wait_for, and main_loop must exit 1 with a RED report."""

        async def fake_hang(*args, **kwargs):
            # Hang for longer than any reasonable deadline
            await asyncio.sleep(60)
            return healthcheck.ProbeResult("mock", healthcheck.Status.GREEN)

        for name in [
            "probe_nginx",
            "probe_gateway",
            "probe_frontend",
            "probe_ali_kernel",
            "probe_llama_loopback",
            "probe_llama_vram",
            "probe_containers",
            "probe_attestation",
        ]:
            monkeypatch.setattr(healthcheck, name, fake_hang)

        # Patch the deadline module-global so we can prove the trigger without
        # sitting in CI for 60s.
        monkeypatch.setattr(healthcheck, "CYCLE_DEADLINE_SEC", 0.5)

        from types import SimpleNamespace

        args = SimpleNamespace(interval=30, once=True)
        # main_loop with once=True returns after the first cycle. The cycle
        # itself is bounded by asyncio.wait_for(CYCLE_DEADLINE_SEC=0.5), so
        # the hang should be cut off and we should exit 1.
        runner = asyncio.create_task(healthcheck.main_loop(args))
        result = await asyncio.wait_for(runner, timeout=5)
        assert result == 1, f"expected exit 1 (hung cycle), got {result}"


class TestArgValidation:
    def test_interval_must_be_positive(self):
        """--interval 0 or negative would create a tight loop and saturate the
        probe targets. argparse should reject it."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--interval", "0"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "--interval must be >= 1" in result.stderr

    def test_deadline_must_exceed_interval(self):
        """HEALTHCHECK_CYCLE_DEADLINE_SEC < --interval would cause the watchdog
        to self-abort on every normal cycle."""
        import subprocess

        env = {**__import__("os").environ, "HEALTHCHECK_CYCLE_DEADLINE_SEC": "1"}
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--interval", "30"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "must be >= --interval" in result.stderr
