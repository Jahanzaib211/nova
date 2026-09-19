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
import subprocess
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
        report.add(healthcheck.ProbeResult("P8_binary_attestation", healthcheck.Status.RED, "drift"))
        healthcheck.dispatch_fixes(report, state)
        assert not any(p.fixed for p in report.probes)
        assert state.consecutive_red["P8_binary_attestation"] == 1

    def test_two_consecutive_reds_triggers_attestation_fix(self, tmp_path, monkeypatch):
        # Patch fix_binary_attestation to use a fake moat under tmp_path, then verify
        # the moat was cleared after two consecutive RED cycles.
        import shutil as _shutil

        moat = tmp_path / "ali-ram-moat"
        moat.mkdir()
        (moat / "golden_binary.hash").write_text("fake_sealed_hash")

        def patched_fix():
            healthcheck.log.warning("auto-fix: clearing test moat")
            _shutil.rmtree(moat, ignore_errors=True)
            return True

        monkeypatch.setattr(healthcheck, "fix_binary_attestation", patched_fix)

        # Run two cycles of RED for P8_binary_attestation
        state = healthcheck.WatchdogState()
        for cycle in (1, 2):
            report = healthcheck.CycleReport(cycle_id=cycle, started_at=0.0, duration_ms=0.0)
            report.add(healthcheck.ProbeResult("P8_binary_attestation", healthcheck.Status.RED, "drift"))
            healthcheck.dispatch_fixes(report, state)

        assert not moat.exists(), "moat should have been cleared by the auto-fix"

    def test_yellow_resets_red_counter(self):
        state = healthcheck.WatchdogState()
        state.consecutive_red["P8_binary_attestation"] = 1
        report = healthcheck.CycleReport(cycle_id=2, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult("P8_binary_attestation", healthcheck.Status.YELLOW))
        healthcheck.dispatch_fixes(report, state)
        assert state.consecutive_red.get("P8_binary_attestation", 0) == 0
        assert state.consecutive_yellow.get("P8_binary_attestation", 0) == 1


# ---------------------------------------------------------------------------
# Repair circuit breaker
#
# Regression cover for 2026-08-12: a repair that could never succeed (pm2 app
# "deerflow" did not exist; ~/Desktop/dify and ~/Desktop/llama-bridge were
# gone) was retried every cycle forever, spawning ~281 subprocesses/minute
# until the machine exhausted RAM + swap and froze.
# ---------------------------------------------------------------------------


class TestFixCircuitBreaker:
    def _red_cycle(self, state, probe="P11_dify"):
        state.cycle_id += 1
        report = healthcheck.CycleReport(cycle_id=state.cycle_id, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult(probe, healthcheck.Status.RED, "down", 1.0))
        healthcheck.dispatch_fixes(report, state)
        return report

    def test_failing_repair_backs_off_instead_of_retrying_every_cycle(self, monkeypatch):
        attempts: list[int] = []
        monkeypatch.setattr(healthcheck, "fix_dify", lambda: attempts.append(1) or False)

        state = healthcheck.WatchdogState()
        for _ in range(12):
            self._red_cycle(state)

        # Without backoff this would be 11 attempts (every cycle after the
        # first). Backoff is exponential, so it must be far fewer.
        assert len(attempts) < 6, f"repair retried too eagerly: {len(attempts)}"
        assert state.fix_failures["P11_dify"] == len(attempts)

    def test_circuit_opens_after_repeated_failures_and_stops_all_attempts(self, monkeypatch):
        attempts: list[int] = []
        monkeypatch.setattr(healthcheck, "fix_dify", lambda: attempts.append(1) or False)

        state = healthcheck.WatchdogState()
        for _ in range(400):
            self._red_cycle(state)

        assert "P11_dify" in state.fix_circuit_open
        assert len(attempts) == healthcheck.FIX_FAILURE_LIMIT
        # Once open, further cycles must not spawn a single extra attempt.
        before = len(attempts)
        for _ in range(50):
            self._red_cycle(state)
        assert len(attempts) == before

    def test_recovery_closes_the_circuit(self, monkeypatch):
        monkeypatch.setattr(healthcheck, "fix_dify", lambda: False)

        state = healthcheck.WatchdogState()
        for _ in range(400):
            self._red_cycle(state)
        assert "P11_dify" in state.fix_circuit_open

        # Probe goes GREEN on its own -> breaker resets, repairs re-armed.
        state.cycle_id += 1
        report = healthcheck.CycleReport(cycle_id=state.cycle_id, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult("P11_dify", healthcheck.Status.GREEN, "ok", 1.0))
        healthcheck.dispatch_fixes(report, state)

        assert "P11_dify" not in state.fix_circuit_open
        assert "P11_dify" not in state.fix_failures

        attempts: list[int] = []
        monkeypatch.setattr(healthcheck, "fix_dify", lambda: attempts.append(1) or True)
        for _ in range(2):
            self._red_cycle(state)
        assert attempts == [1]

    def test_successful_repair_leaves_breaker_clean(self, monkeypatch):
        monkeypatch.setattr(healthcheck, "fix_dify", lambda: True)
        state = healthcheck.WatchdogState()
        for _ in range(5):
            self._red_cycle(state)
        assert state.fix_failures.get("P11_dify", 0) == 0
        assert "P11_dify" not in state.fix_circuit_open


class TestDisabledProbes:
    def test_empty_by_default(self, monkeypatch):
        monkeypatch.delenv("HEALTHCHECK_DISABLED_PROBES", raising=False)
        assert healthcheck.disabled_probes() == set()

    def test_parses_and_strips_names(self, monkeypatch):
        monkeypatch.setenv("HEALTHCHECK_DISABLED_PROBES", " P9_bridge , P11_dify ,, P12_tunnel ")
        assert healthcheck.disabled_probes() == {"P9_bridge", "P11_dify", "P12_tunnel"}

    @pytest.mark.asyncio
    async def test_disabled_probes_are_not_run(self, monkeypatch):
        ran: list[str] = []

        async def fake_green(*args, **kwargs):
            return healthcheck.ProbeResult("mock", healthcheck.Status.GREEN, "mock-ok", 1.0)

        for name in [
            "probe_nginx",
            "probe_gateway",
            "probe_frontend",
            "probe_local_llm_gateway",
            "probe_llama_loopback",
            "probe_llama_vram",
            "probe_containers",
            "probe_binary_attestation",
            "probe_litellm",
            "probe_drift",
        ]:
            monkeypatch.setattr(healthcheck, name, fake_green)

        # These three must never be invoked at all — not merely ignored.
        for name in ["probe_llama_bridge", "probe_dify", "probe_tunnel"]:

            async def spy(*args, _n=name, **kwargs):
                ran.append(_n)
                return await fake_green()

            monkeypatch.setattr(healthcheck, name, spy)

        monkeypatch.setenv("HEALTHCHECK_DISABLED_PROBES", "P9_bridge,P11_dify,P12_tunnel")
        report = await healthcheck.run_cycle(healthcheck.WatchdogState())

        assert ran == []
        assert len(report.probes) == len(healthcheck.build_probe_factories()) - len(healthcheck.disabled_probes())
        assert {p.name for p in report.probes}.isdisjoint({"P9_bridge", "P11_dify", "P12_tunnel"})


# ---------------------------------------------------------------------------
# fix_binary_attestation behavior (no real systemd needed)
# ---------------------------------------------------------------------------


class TestFixBinaryAttestation:
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

        result = healthcheck.fix_binary_attestation()
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
            "probe_local_llm_gateway",
            "probe_llama_loopback",
            "probe_llama_vram",
            "probe_containers",
            "probe_binary_attestation",
            "probe_llama_bridge",
            "probe_litellm",
            "probe_dify",
            "probe_tunnel",
            "probe_drift",
            "probe_searxng",
        ]:
            monkeypatch.setattr(healthcheck, name, fake_green)

        state = healthcheck.WatchdogState()
        report = await healthcheck.run_cycle(state)
        assert report.overall == healthcheck.Status.GREEN
        # Derived, not hardcoded: a literal here makes every added probe fail
        # three tests with `assert 14 == 13`, which names neither the probe
        # nor the reason.
        assert len(report.probes) == len(healthcheck.build_probe_factories())
        assert all(p.status == healthcheck.Status.GREEN for p in report.probes)

    @pytest.mark.asyncio
    async def test_run_cycle_survives_single_probe_exception(self, monkeypatch):
        """A single probe raising must not kill the whole cycle — the JSON
        status line should still emit, the other probes should be GREEN,
        and the failed probe should appear as a labeled RED."""

        async def fake_green(*args, **kwargs):
            return healthcheck.ProbeResult("mock", healthcheck.Status.GREEN, "mock-ok", 1.0)

        async def fake_boom(*args, **kwargs):
            raise RuntimeError("simulated probe failure")

        for name in [
            "probe_nginx",
            "probe_gateway",
            "probe_frontend",
            "probe_local_llm_gateway",
            "probe_llama_loopback",
            "probe_llama_vram",
            "probe_containers",
            "probe_llama_bridge",
            "probe_litellm",
            "probe_dify",
            "probe_tunnel",
            "probe_drift",
            "probe_searxng",
        ]:
            monkeypatch.setattr(healthcheck, name, fake_green)
        monkeypatch.setattr(healthcheck, "probe_binary_attestation", fake_boom)

        state = healthcheck.WatchdogState()
        report = await healthcheck.run_cycle(state)

        # The attestation probe should appear as RED with its real name (P8_binary_attestation),
        # not some auto-generated placeholder. This is the regression that
        # motivated the (name, coroutine) pair refactor.
        by_name = {p.name: p for p in report.probes}
        assert "P8_binary_attestation" in by_name
        assert by_name["P8_binary_attestation"].status == healthcheck.Status.RED
        assert "RuntimeError" in by_name["P8_binary_attestation"].detail
        # The other 12 should still be GREEN and the cycle should still report.
        assert report.overall == healthcheck.Status.RED
        assert report.exit_code == 1
        green_count = sum(1 for p in report.probes if p.status == healthcheck.Status.GREEN)
        # every probe but the one made to raise
        assert green_count == len(healthcheck.build_probe_factories()) - 1


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
            cwd=SCRIPT_PATH.parents[1],
        )
        assert "test_log_routing_marker" in result.stderr, f"expected marker in stderr, got stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "test_log_routing_marker" not in result.stdout, f"marker leaked to stdout: {result.stdout!r}"

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
            "probe_local_llm_gateway",
            "probe_llama_loopback",
            "probe_llama_vram",
            "probe_containers",
            "probe_binary_attestation",
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


class TestLitellmFix:
    def test_fix_litellm_restarts_registered_app(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

            class R:
                returncode = 0
                stdout = "script path  x"

            return R()

        monkeypatch.setattr(healthcheck.subprocess, "run", fake_run)
        assert healthcheck.fix_litellm() is True
        assert ["pm2", "restart", "nova-litellm"] in calls

    def test_fix_litellm_starts_from_ecosystem_when_missing(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

            class R:
                returncode = 1 if cmd[:2] == ["pm2", "describe"] else 0
                stdout = ""

            return R()

        monkeypatch.setattr(healthcheck.subprocess, "run", fake_run)
        assert healthcheck.fix_litellm() is True
        assert any(cmd[:2] == ["pm2", "start"] and "--only" in cmd and "nova-litellm" in cmd for cmd in calls)


class TestDifyProbeAndFix:
    @pytest.mark.asyncio
    async def test_probe_dify_green_on_finished_setup(self, monkeypatch):
        async def fake_http_probe(name, url, body_validator=None, **kwargs):
            assert name == "P11_dify"
            assert "/console/api/setup" in url
            ok = body_validator({"step": "finished", "setup_at": "2026-07-06"})
            return healthcheck.ProbeResult(name, healthcheck.Status.GREEN if ok else healthcheck.Status.RED, "ok", 1.0)

        monkeypatch.setattr(healthcheck, "_http_probe", fake_http_probe)
        result = await healthcheck.probe_dify()
        assert result.status == healthcheck.Status.GREEN

    @pytest.mark.asyncio
    async def test_probe_dify_validator_rejects_uninitialized(self, monkeypatch):
        """step=not_started (fresh DB, migrations pending) must not read as
        healthy — that state means the api is up but the deployment isn't."""
        captured = {}

        async def fake_http_probe(name, url, body_validator=None, **kwargs):
            captured["validator"] = body_validator
            return healthcheck.ProbeResult(name, healthcheck.Status.GREEN, "ok", 1.0)

        monkeypatch.setattr(healthcheck, "_http_probe", fake_http_probe)
        await healthcheck.probe_dify()
        assert captured["validator"]({"step": "finished"}) is True
        assert captured["validator"]({"step": "not_started", "setup_at": None}) is False
        assert captured["validator"]({}) is False

    def test_fix_dify_restarts_registered_app(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

            class R:
                returncode = 0
                stdout = "script path  x"

            return R()

        monkeypatch.setattr(healthcheck.subprocess, "run", fake_run)
        assert healthcheck.fix_dify() is True
        assert ["pm2", "restart", "nova-dify"] in calls

    def test_fix_dify_starts_from_ecosystem_when_missing(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

            class R:
                returncode = 1 if cmd[:2] == ["pm2", "describe"] else 0
                stdout = ""

            return R()

        monkeypatch.setattr(healthcheck.subprocess, "run", fake_run)
        assert healthcheck.fix_dify() is True
        assert any(cmd[:2] == ["pm2", "start"] and "--only" in cmd and "nova-dify" in cmd for cmd in calls)

    def test_dispatch_fixes_heals_red_p11_after_two_cycles(self, monkeypatch):
        fixed: list[str] = []
        monkeypatch.setattr(healthcheck, "fix_dify", lambda: fixed.append("dify") or True)

        state = healthcheck.WatchdogState()
        for _ in range(2):
            report = healthcheck.CycleReport(cycle_id=1, started_at=0.0, duration_ms=0.0)
            report.add(healthcheck.ProbeResult("P11_dify", healthcheck.Status.RED, "down", 1.0))
            healthcheck.dispatch_fixes(report, state)

        assert fixed == ["dify"]
        assert report.probes[0].fixed is True


# ---------------------------------------------------------------------------
# Post-repair grace + "docker ps timeout" is not a restart trigger
#
# Regression cover for 2026-09-13: host rebooted, docker was slow, P7 went RED
# on "docker ps timeout" twice, and the daemon issued `pm2 restart nova` at
# 08:29:15 and again at 08:29:40. The second restart interrupted the first
# compose-up mid-flight and stranded deer-flow-gateway (stopped, never
# restarted) for four hours.
# ---------------------------------------------------------------------------


class TestRepairGrace:
    def _red_cycle(self, state, probe="P7_containers", detail="only 1 containers", **kw):
        state.cycle_id += 1
        report = healthcheck.CycleReport(cycle_id=state.cycle_id, started_at=0.0, duration_ms=0.0)
        report.add(healthcheck.ProbeResult(probe, healthcheck.Status.RED, detail, 1.0, **kw))
        healthcheck.dispatch_fixes(report, state)
        return report

    def test_successful_repair_is_not_repeated_within_grace(self, monkeypatch):
        attempts: list[int] = []
        monkeypatch.setattr(healthcheck, "fix_deerflow_containers", lambda: attempts.append(1) or True)
        state = healthcheck.WatchdogState()
        # Stack stays RED for the whole grace window while it boots.
        for _ in range(2 + healthcheck.FIX_GRACE_CYCLES - 1):
            self._red_cycle(state)
        assert attempts == [1], f"repair re-issued during grace: {len(attempts)}"
        # Once the grace window has elapsed and it is *still* RED, repair again.
        self._red_cycle(state)
        assert attempts == [1, 1]

    def test_docker_ps_timeout_never_restarts_the_stack(self, monkeypatch):
        attempts: list[int] = []
        monkeypatch.setattr(healthcheck, "fix_deerflow_containers", lambda: attempts.append(1) or True)
        state = healthcheck.WatchdogState()
        for _ in range(10):
            self._red_cycle(state, detail="docker ps timeout", fixable=False)
        assert attempts == []
        # It still counts as RED for the dashboard.
        assert state.consecutive_red["P7_containers"] == 10

    @pytest.mark.asyncio
    async def test_probe_containers_marks_timeout_unfixable(self, monkeypatch):
        class _Hung:
            async def communicate(self):
                await asyncio.sleep(60)

        async def _spawn(*a, **k):
            return _Hung()

        monkeypatch.setattr(healthcheck.asyncio, "create_subprocess_exec", _spawn)

        async def _wait_for(coro, timeout):
            coro.close()
            raise TimeoutError

        monkeypatch.setattr(healthcheck.asyncio, "wait_for", _wait_for)
        res = await healthcheck.probe_containers()
        assert res.status == healthcheck.Status.RED
        assert res.detail == "docker ps timeout"
        assert res.fixable is False

    def test_stack_restart_refused_when_nova_restarted_recently(self, monkeypatch):
        healed: list[str] = []
        monkeypatch.setattr(healthcheck, "_pm2_app_age_sec", lambda app: 12.0)
        monkeypatch.setattr(healthcheck, "_heal_pm2_app", lambda app, **kw: healed.append(app) or True)
        assert healthcheck.fix_deerflow_containers() is False
        assert healed == []

    def test_stack_restart_allowed_when_nova_is_old(self, monkeypatch):
        healed: list[str] = []
        monkeypatch.setattr(healthcheck, "_pm2_app_age_sec", lambda app: 3600.0)
        monkeypatch.setattr(healthcheck, "_heal_pm2_app", lambda app, **kw: healed.append(app) or True)
        assert healthcheck.fix_deerflow_containers() is True
        assert healed == ["nova"]


class TestFixGatewayContainer:
    def _fake_docker(self, monkeypatch, status: str | None):
        calls: list[list[str]] = []

        def _run(cmd, **kw):
            calls.append(cmd)
            if cmd[:2] == ["docker", "inspect"]:
                if status is None:
                    return subprocess.CompletedProcess(cmd, 1, "", "No such object")
                return subprocess.CompletedProcess(cmd, 0, status + "\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(healthcheck.subprocess, "run", _run)
        return calls

    def test_starts_a_stopped_container(self, monkeypatch):
        calls = self._fake_docker(monkeypatch, "exited")
        assert healthcheck.fix_gateway_container() is True
        assert ["docker", "start", healthcheck.GATEWAY_CONTAINER] in calls

    def test_leaves_a_running_container_alone(self, monkeypatch):
        calls = self._fake_docker(monkeypatch, "running")
        assert healthcheck.fix_gateway_container() is False
        assert not any(c[:2] in (["docker", "start"], ["docker", "restart"]) for c in calls)

    def test_missing_container_is_left_to_p7(self, monkeypatch):
        calls = self._fake_docker(monkeypatch, None)
        assert healthcheck.fix_gateway_container() is False
        assert not any(c[:2] == ["docker", "start"] for c in calls)

    def test_dispatch_wires_p2_to_gateway_fix(self, monkeypatch):
        attempts: list[int] = []
        monkeypatch.setattr(healthcheck, "fix_gateway_container", lambda: attempts.append(1) or True)
        state = healthcheck.WatchdogState()
        for _ in range(2):
            state.cycle_id += 1
            report = healthcheck.CycleReport(cycle_id=state.cycle_id, started_at=0.0, duration_ms=0.0)
            report.add(healthcheck.ProbeResult("P2_gateway", healthcheck.Status.RED, "ConnectError", 1.0))
            healthcheck.dispatch_fixes(report, state)
        assert attempts == [1]
        assert report.probes[0].fixed is True
        assert "docker start gateway" in report.probes[0].detail


class TestJobsWorkerProbe:
    """P15_jobs_worker: the job runner is a separate container; a missing or
    stale worker row means background work (campaign sends, imports) silently
    stops while every other probe stays green."""

    def _client(self, payload, status_code=200):
        class R:
            def __init__(self):
                self.status_code = status_code

            def json(self):
                return payload

        class C:
            def __init__(self, *a, **k):
                self.headers = k.get("headers") or {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                self.headers = headers or {}
                return R()

        return C

    @pytest.mark.anyio
    async def test_green_with_a_fresh_worker(self, monkeypatch):
        client = self._client({"queued": 0, "running": 1, "retrying": 0, "dead_letter": 0, "workers": [{"worker_id": "w1", "last_seen_age_s": 4.0}]})
        monkeypatch.setattr(healthcheck.httpx, "AsyncClient", client)
        monkeypatch.setenv("NOVA_OPS_TOKEN", "tok")
        healthcheck._jobs_dead_letter_seen = None
        res = await healthcheck.probe_jobs_worker()
        assert res.status == healthcheck.Status.GREEN
        assert "1 worker" in res.detail

    @pytest.mark.anyio
    async def test_red_when_no_worker_is_fresh(self, monkeypatch):
        client = self._client({"queued": 3, "running": 0, "retrying": 0, "dead_letter": 0, "workers": [{"worker_id": "w1", "last_seen_age_s": 400.0}]})
        monkeypatch.setattr(healthcheck.httpx, "AsyncClient", client)
        monkeypatch.setenv("NOVA_OPS_TOKEN", "tok")
        res = await healthcheck.probe_jobs_worker()
        assert res.status == healthcheck.Status.RED
        assert "no live worker" in res.detail and "3 queued" in res.detail

    @pytest.mark.anyio
    async def test_yellow_when_dead_letter_grows(self, monkeypatch):
        client = self._client({"queued": 0, "running": 0, "retrying": 0, "dead_letter": 2, "workers": [{"worker_id": "w1", "last_seen_age_s": 1.0}]})
        monkeypatch.setattr(healthcheck.httpx, "AsyncClient", client)
        monkeypatch.setenv("NOVA_OPS_TOKEN", "tok")
        healthcheck._jobs_dead_letter_seen = 1
        res = await healthcheck.probe_jobs_worker()
        assert res.status == healthcheck.Status.YELLOW and "dead-letter" in res.detail
        # steady state afterwards: same count → green again
        res = await healthcheck.probe_jobs_worker()
        assert res.status == healthcheck.Status.GREEN

    @pytest.mark.anyio
    async def test_skipped_without_ops_token(self, monkeypatch):
        monkeypatch.setattr(healthcheck, "_ops_token", lambda: "")
        res = await healthcheck.probe_jobs_worker()
        assert res.status == healthcheck.Status.GREEN and "skipped" in res.detail

    def test_ops_token_falls_back_to_dotenv(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NOVA_OPS_TOKEN", raising=False)
        (tmp_path / ".env").write_text('OTHER=1\nNOVA_OPS_TOKEN="from-dotenv"\n', encoding="utf-8")
        monkeypatch.setattr(healthcheck, "__file__", str(tmp_path / "scripts" / "healthcheck-daemon.py"))
        assert healthcheck._ops_token() == "from-dotenv"
        monkeypatch.setenv("NOVA_OPS_TOKEN", "from-env")
        assert healthcheck._ops_token() == "from-env"

    def test_registered_with_a_fixer(self):
        names = [n for n, _ in healthcheck.build_probe_factories()]
        assert "P15_jobs_worker" in names
        assert "P15_jobs_worker" in healthcheck.FIX_DISPATCH

    def test_fix_restarts_a_stopped_jobs_container(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

            class R:
                returncode = 0
                stdout = "exited"

            return R()

        monkeypatch.setattr(healthcheck.subprocess, "run", fake_run)
        assert healthcheck.fix_jobs_container() is True
        assert ["docker", "restart", healthcheck.JOBS_CONTAINER] in calls

    def test_fix_leaves_a_running_container_alone(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = "running"

            return R()

        monkeypatch.setattr(healthcheck.subprocess, "run", fake_run)
        assert healthcheck.fix_jobs_container() is False
