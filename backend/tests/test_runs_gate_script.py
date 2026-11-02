"""Unit tests for scripts/gates/runs-gate.py (runs.json producer).

The gate exists because nothing else in the stack looks at a run: on
2026-11-01 run 3cc50519 sat ``running`` with 311 LLM calls and zero
persisted ``run_events`` and every other surface said green.

These tests cover the parts that can be decided without a database (the
envelope, the severity mapping, the dialect branching, the percentile, the
provider-count reader) and drive the SQL checks against a temporary sqlite
database, so both engines' SQL is exercised.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "runs-gate.py"


@pytest.fixture
def gate():
    spec = importlib.util.spec_from_file_location("runs_gate_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["runs_gate_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sqlite_db(tmp_path: Path, rows: list[tuple], events: list[tuple]) -> Path:
    db = tmp_path / "deerflow.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE runs (run_id TEXT, status TEXT, llm_call_count INTEGER, created_at TEXT, updated_at TEXT, error TEXT)")
    conn.execute("CREATE TABLE run_events (id INTEGER, run_id TEXT, event_type TEXT, created_at TEXT)")
    # Rows may omit the trailing ``error`` column.
    conn.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?)", [tuple(row) + (None,) * (6 - len(row)) for row in rows])
    conn.executemany("INSERT INTO run_events VALUES (?,?,?,?)", events)
    conn.commit()
    conn.close()
    return db


def _utc_ago(seconds: int) -> str:
    """A ``YYYY-MM-DD HH:MM:SS`` UTC timestamp ``seconds`` in the past (sqlite's datetime format)."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - seconds))


def _wire_sqlite(gate, monkeypatch, tmp_path, rows, events=()):
    """Point the gate's sqlite reader at a temporary database."""
    db = _sqlite_db(tmp_path, rows, list(events))
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    (tmp_path / "backend" / ".deer-flow" / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "backend" / ".deer-flow" / "data" / "deerflow.db").write_bytes(db.read_bytes())
    monkeypatch.setattr(gate, "_active_backend", lambda: ("sqlite", None))
    return gate.SqliteDialect()


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------


def test_check_helper_shape(gate):
    item = gate.check("stuck_runs", gate.RED, "one stuck run", stuck=1)
    assert item["name"] == "stuck_runs"
    assert item["status"] == gate.RED
    assert item["detail"] == "one stuck run"
    assert item["stuck"] == 1


def test_report_envelope_is_exactly_the_gate_contract(gate, monkeypatch):
    monkeypatch.setattr(gate, "CHECKS", ())
    report = gate.build_report()
    assert set(report) == {"gate", "checked_at_epoch", "overall", "ok", "checks"}
    assert report["gate"] == "runs"
    assert report["overall"] == gate.GREEN
    assert report["ok"] is True
    assert isinstance(report["checked_at_epoch"], float)
    assert report["checks"] == []


def test_report_overall_is_red_when_any_check_is_red(gate, monkeypatch):
    monkeypatch.setattr(
        gate,
        "CHECKS",
        (
            lambda _d: gate.check("a", gate.GREEN, "fine"),
            lambda _d: gate.check("b", gate.RED, "broken"),
        ),
    )
    report = gate.build_report()
    assert report["overall"] == gate.RED
    assert report["ok"] is False


def test_report_overall_is_yellow_when_no_check_is_red(gate, monkeypatch):
    monkeypatch.setattr(
        gate,
        "CHECKS",
        (
            lambda _d: gate.check("a", gate.GREEN, "fine"),
            lambda _d: gate.check("b", gate.YELLOW, "meh"),
        ),
    )
    report = gate.build_report()
    assert report["overall"] == gate.YELLOW
    assert report["ok"] is True


def test_a_check_that_raises_becomes_yellow_not_a_crash(gate, monkeypatch):
    def boom(_dialect):
        raise RuntimeError("database on fire")

    monkeypatch.setattr(gate, "CHECKS", (boom,))
    report = gate.build_report()
    assert report["overall"] == gate.YELLOW
    assert "database on fire" in report["checks"][0]["detail"]


def test_write_atomic_produces_a_readable_document(gate, tmp_path):
    target = tmp_path / "nested" / "runs.json"
    gate.write_atomic(target, {"gate": "runs", "overall": "green"})
    assert json.loads(target.read_text())["gate"] == "runs"
    # No temporary file left behind.
    assert [p.name for p in target.parent.iterdir()] == ["runs.json"]


# ---------------------------------------------------------------------------
# dialect
# ---------------------------------------------------------------------------


def test_dialects_branch_only_where_sql_differs(gate):
    pg, lite = gate.PostgresDialect(), gate.SqliteDialect()
    assert pg.now() == "now()"
    assert "interval" in pg.seconds_ago(1800)
    assert "datetime('now'" in lite.seconds_ago(1800)
    assert "EXTRACT" in pg.seconds_between("updated_at", "created_at")
    assert "strftime" in lite.seconds_between("updated_at", "created_at")


def test_backend_selection_follows_config_yaml(gate, monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text("database:\n  backend: postgres\n  postgres_url: $DATABASE_URL\n")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    backend, url = gate._active_backend()
    assert backend == "postgres"
    assert url is None or isinstance(url, str)


# ---------------------------------------------------------------------------
# stuck_runs
# ---------------------------------------------------------------------------


def test_stuck_runs_is_green_when_nothing_is_stuck(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[("r1", "success", 3, "2026-11-01 10:00:00", "2026-11-01 10:05:00")],
    )
    result = gate.check_stuck_runs(dialect)
    assert result["status"] == gate.GREEN
    assert result["stuck"] == 0


def test_stuck_runs_is_red_and_names_the_run(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[
            ("stuck-1", "running", 12, "2026-01-01 00:00:00", "2026-01-01 00:00:00"),
            ("ok-1", "success", 3, "2026-01-01 00:00:00", "2026-01-01 00:05:00"),
        ],
    )
    result = gate.check_stuck_runs(dialect)
    assert result["status"] == gate.RED
    assert result["stuck"] == 1
    assert result["run_ids"] == ["stuck-1"]
    assert "stuck-1" in result["detail"]


def test_stuck_runs_is_yellow_when_the_query_cannot_run(gate, monkeypatch):
    monkeypatch.setattr(gate, "_query_rows", lambda _d, _q: None)
    result = gate.check_stuck_runs(gate.PostgresDialect())
    assert result["status"] == gate.YELLOW
    assert "could not query" in result["detail"]


def test_stuck_run_threshold_is_env_overridable(gate, monkeypatch, tmp_path):
    monkeypatch.setenv("NOVA_GATE_STUCK_RUN_SECONDS", "10")
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[("r1", "running", 1, "2026-11-01 00:00:00", "2026-11-01 00:00:00")],
    )
    result = gate.check_stuck_runs(dialect)
    assert result["threshold_sec"] == 10
    assert result["status"] == gate.RED


# ---------------------------------------------------------------------------
# journal_persistence
# ---------------------------------------------------------------------------


def test_journal_is_green_when_events_were_persisted(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[("r1", "success", 5, "2026-11-01 10:00:00", "2026-11-01 10:05:00")],
        events=[(1, "r1", "message", "2026-11-01 10:01:00")],
    )
    result = gate.check_journal_persistence(dialect)
    assert result["status"] == gate.GREEN
    assert result["missing"] == 0


def test_journal_is_red_for_a_run_with_llm_calls_and_no_events(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[
            ("silent", "running", 311, "2026-11-01 10:00:00", "2026-11-01 10:05:00"),
            ("loud", "success", 4, "2026-11-01 10:00:00", "2026-11-01 10:05:00"),
        ],
        events=[(1, "loud", "message", "2026-11-01 10:01:00")],
    )
    result = gate.check_journal_persistence(dialect)
    assert result["status"] == gate.RED
    assert result["run_ids"] == ["silent"]
    assert "silent" in result["detail"]


def test_journal_ignores_an_old_terminal_run_outside_the_window(gate, monkeypatch, tmp_path):
    """History is not the signal: a run that lost its events last week
    cannot be repaired by a gate, and would keep it red forever."""
    monkeypatch.setenv("NOVA_GATE_JOURNAL_WINDOW_SECONDS", "60")
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[("old", "error", 36, "2020-01-01 00:00:00", "2020-01-01 00:10:00")],
    )
    result = gate.check_journal_persistence(dialect)
    assert result["status"] == gate.GREEN


def test_journal_catches_a_terminal_run_inside_the_window(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        # Relative to now: a fixed date passes on the day it is written and
        # fails once it ages out of the 24 h window.
        rows=[("recent", "error", 36, _utc_ago(3600), _utc_ago(3000))],
    )
    monkeypatch.setenv("NOVA_GATE_JOURNAL_WINDOW_SECONDS", "86400")
    result = gate.check_journal_persistence(dialect)
    assert result["status"] == gate.RED
    assert result["run_ids"] == ["recent"]


# ---------------------------------------------------------------------------
# multi_instance_providers
# ---------------------------------------------------------------------------


def test_provider_count_is_read_from_the_current_boot_only(gate, monkeypatch, tmp_path):
    log = tmp_path / "gateway.log"
    log.write_text("===== gateway boot 2026-11-01T00:00:00Z (pid 1) =====\nmore than one AioSandboxProvider instance in this process (3) - sandbox reaper races possible\n===== gateway boot 2026-11-02T00:00:00Z (pid 1) =====\nall quiet\n")
    monkeypatch.setenv("NOVA_GATE_GATEWAY_LOG", str(log))
    monkeypatch.delenv("NOVA_GATE_LIVE_PROVIDER_INSTANCES", raising=False)
    count, source = gate._live_provider_instance_count()
    assert count == 1
    assert "no multi-instance warning" in source


def test_provider_count_sees_a_multi_instance_warning(gate, monkeypatch, tmp_path):
    log = tmp_path / "gateway.log"
    log.write_text("===== gateway boot 2026-11-02T00:00:00Z (pid 1) =====\nmore than one AioSandboxProvider instance in this process (3) - sandbox reaper races possible\n")
    monkeypatch.setenv("NOVA_GATE_GATEWAY_LOG", str(log))
    monkeypatch.delenv("NOVA_GATE_LIVE_PROVIDER_INSTANCES", raising=False)
    count, _source = gate._live_provider_instance_count()
    assert count == 3
    result = gate.check_multi_instance_providers(gate.PostgresDialect())
    assert result["status"] == gate.YELLOW
    assert result["instances"] == 3


def test_provider_count_is_yellow_when_it_cannot_be_read(gate, monkeypatch, tmp_path):
    monkeypatch.setenv("NOVA_GATE_GATEWAY_LOG", str(tmp_path / "missing.log"))
    monkeypatch.delenv("NOVA_GATE_LIVE_PROVIDER_INSTANCES", raising=False)
    count, _source = gate._live_provider_instance_count()
    assert count is None
    result = gate.check_multi_instance_providers(gate.PostgresDialect())
    assert result["status"] == gate.YELLOW
    assert "cannot read" in result["detail"]


def test_provider_count_honours_an_explicit_override(gate, monkeypatch, tmp_path):
    monkeypatch.setenv("NOVA_GATE_LIVE_PROVIDER_INSTANCES", "2")
    monkeypatch.setenv("NOVA_GATE_GATEWAY_LOG", str(tmp_path / "missing.log"))
    count, source = gate._live_provider_instance_count()
    assert count == 2
    assert source == "NOVA_GATE_LIVE_PROVIDER_INSTANCES"


# ---------------------------------------------------------------------------
# run_completion_latency
# ---------------------------------------------------------------------------


def test_percentile_matches_linear_interpolation(gate):
    values = [1.0, 2.0, 3.0, 4.0]
    assert gate._percentile(values, 0.5) == pytest.approx(2.5)
    assert gate._percentile(values, 0.0) == pytest.approx(1.0)
    assert gate._percentile(values, 1.0) == pytest.approx(4.0)
    assert gate._percentile([], 0.95) == 0.0
    assert gate._percentile([7.0], 0.95) == pytest.approx(7.0)


def test_latency_is_green_below_the_threshold(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[("r1", "success", 1, "2026-11-01 10:00:00", "2026-11-01 10:01:00")],
    )
    result = gate.check_run_completion_latency(dialect)
    assert result["status"] == gate.GREEN
    assert result["sample"] == 1
    assert result["p95_sec"] == pytest.approx(60.0, abs=1)


def test_latency_is_yellow_above_the_threshold(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[("r1", "success", 1, "2026-11-01 10:00:00", "2026-11-01 12:00:00")],
    )
    result = gate.check_run_completion_latency(dialect)
    assert result["status"] == gate.YELLOW
    assert result["p95_sec"] == pytest.approx(7200.0, abs=1)
    assert "p95 threshold" in result["detail"]


def test_latency_is_green_when_there_are_no_terminal_runs(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(gate, monkeypatch, tmp_path, rows=[])
    result = gate.check_run_completion_latency(dialect)
    assert result["status"] == gate.GREEN
    assert result["sample"] == 0


# ---------------------------------------------------------------------------
# review fixes: orphaned runs, bounded queries, reload boundary, pending runs
# ---------------------------------------------------------------------------


def _ago(seconds: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - seconds))


def test_orphan_error_matches_what_the_reaper_writes(gate):
    """The gate matches the reaper's text, so it must be the reaper's text."""
    root = Path(__file__).resolve().parents[1]
    for source in (root / "app" / "gateway" / "deps.py", root / "packages" / "harness" / "nova" / "runtime" / "runs" / "manager.py"):
        assert gate.ORPHAN_ERROR in source.read_text(), source


def test_latency_excludes_runs_reaped_as_orphans(gate, monkeypatch, tmp_path):
    """A reaped run's updated_at is the reap time, not a completion time:
    one restart after a long idle inflated p95 to 6055s."""
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[
            ("ok", "success", 1, "2026-11-01 10:00:00", "2026-11-01 10:01:00", None),
            ("reaped", "error", 1, "2026-11-01 10:00:00", "2026-11-01 13:00:00", gate.ORPHAN_ERROR),
        ],
    )
    result = gate.check_run_completion_latency(dialect)
    assert result["status"] == gate.GREEN
    assert result["sample"] == 1
    assert result["max_sec"] == pytest.approx(60.0, abs=1)


def test_latency_keeps_ordinary_errors(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[("failed", "error", 1, "2026-11-01 10:00:00", "2026-11-01 12:00:00", "provider 500")],
    )
    result = gate.check_run_completion_latency(dialect)
    assert result["sample"] == 1
    assert result["status"] == gate.YELLOW


def test_orphaned_runs_green_when_none(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(gate, monkeypatch, tmp_path, rows=[("ok", "success", 1, _ago(120), _ago(60), None)])
    result = gate.check_orphaned_runs(dialect)
    assert result["name"] == "orphaned_runs"
    assert result["status"] == gate.GREEN
    assert result["recent"] == 0


def test_orphaned_runs_yellow_when_reaped_in_the_last_hour(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[
            ("fresh", "error", 1, _ago(4000), _ago(300), gate.ORPHAN_ERROR),
            ("old", "error", 1, _ago(50000), _ago(40000), gate.ORPHAN_ERROR),
        ],
    )
    result = gate.check_orphaned_runs(dialect)
    assert result["status"] == gate.YELLOW
    assert result["recent"] == 1
    assert result["day"] == 2


def test_orphaned_runs_older_than_an_hour_are_reported_but_green(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(gate, monkeypatch, tmp_path, rows=[("old", "error", 1, _ago(50000), _ago(40000), gate.ORPHAN_ERROR)])
    result = gate.check_orphaned_runs(dialect)
    assert result["status"] == gate.GREEN
    assert result["recent"] == 0
    assert result["day"] == 1
    assert "1 in the last" in result["detail"]


def test_orphaned_runs_is_registered(gate):
    assert gate.check_orphaned_runs in gate.CHECKS


def test_orphaned_runs_yellow_when_the_query_cannot_run(gate, monkeypatch):
    monkeypatch.setattr(gate, "_query_rows", lambda _d, _q: None)
    result = gate.check_orphaned_runs(gate.PostgresDialect())
    assert result["status"] == gate.YELLOW


def test_stuck_pending_run_is_yellow_not_red(gate, monkeypatch, tmp_path):
    """Queued-but-never-started is a backlog, not a dead worker."""
    dialect = _wire_sqlite(gate, monkeypatch, tmp_path, rows=[("queued", "pending", 0, "2026-01-01 00:00:00", "2026-01-01 00:00:00")])
    result = gate.check_stuck_runs(dialect)
    assert result["status"] == gate.YELLOW
    assert result["pending"] == 1
    assert result["running"] == 0
    assert "queued" in result["detail"]


def test_stuck_running_run_is_red_even_beside_a_pending_one(gate, monkeypatch, tmp_path):
    dialect = _wire_sqlite(
        gate,
        monkeypatch,
        tmp_path,
        rows=[
            ("queued", "pending", 0, "2026-01-01 00:00:00", "2026-01-01 00:00:00"),
            ("dead", "running", 9, "2026-01-01 00:00:00", "2026-01-01 00:00:00"),
        ],
    )
    result = gate.check_stuck_runs(dialect)
    assert result["status"] == gate.RED
    assert result["running"] == 1
    assert result["pending"] == 1
    assert result["stuck"] == 2


def _capture_psql(gate, monkeypatch, *, raises=None):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if raises is not None:
            raise raises
        return gate.subprocess.CompletedProcess(cmd, 0, "1\n", "")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    return calls


def test_psql_is_bounded_well_under_the_producer_timeout(gate, monkeypatch):
    """Six queries x 30s could outlive the daemon's 60s producer timeout."""
    calls = _capture_psql(gate, monkeypatch)
    assert gate._psql("SELECT 1") == "1\n"
    cmd, kwargs = calls[0]
    assert kwargs["timeout"] <= 10
    assert "close_fds" not in kwargs
    joined = " ".join(cmd)
    assert "PGCONNECT_TIMEOUT=" in joined
    assert "statement_timeout=" in joined
    # The worst case over every query the gate can issue stays under 60s.
    assert gate.PSQL_TIMEOUT_S * gate.MAX_QUERIES < 60


def test_psql_timeout_marks_postgres_unavailable_and_short_circuits(gate, monkeypatch):
    calls = _capture_psql(gate, monkeypatch, raises=gate.subprocess.TimeoutExpired(["psql"], 8))
    assert gate._psql("SELECT 1") is None
    assert gate._psql("SELECT 2") is None
    assert len(calls) == 1, "after one timeout the remaining queries must not each wait again"
    result = gate.check_stuck_runs(gate.PostgresDialect())
    assert result["status"] == gate.YELLOW
    assert "unavailable" in result["detail"]
    assert "timed out" in result["detail"]


def test_report_never_crashes_when_postgres_hangs(gate, monkeypatch):
    _capture_psql(gate, monkeypatch, raises=gate.subprocess.TimeoutExpired(["psql"], 8))
    monkeypatch.setattr(gate, "_active_backend", lambda: ("postgres", None))
    monkeypatch.setenv("NOVA_GATE_LIVE_PROVIDER_INSTANCES", "1")
    report = gate.build_report()
    assert report["overall"] == gate.YELLOW
    db_checks = [c for c in report["checks"] if c["name"] != "multi_instance_providers"]
    assert db_checks and all(c["status"] == gate.YELLOW and "unavailable" in c["detail"] for c in db_checks)


def test_a_reload_is_a_boot_boundary(gate, monkeypatch, tmp_path):
    """uvicorn --reload restarts the worker without the entrypoint banner, so
    a warning from before the reload must not stay sticky."""
    log = tmp_path / "gateway.log"
    log.write_text(
        "===== gateway boot 2026-11-01T00:00:00Z (pid 1) =====\n"
        "INFO:     Started server process [57]\n"
        "more than one AioSandboxProvider instance in this process (3) - races\n"
        "WARNING:  WatchFiles detected changes in 'x.py'. Reloading...\n"
        "INFO:     Started server process [7533]\n"
        "INFO:     Application startup complete.\n"
    )
    monkeypatch.setenv("NOVA_GATE_GATEWAY_LOG", str(log))
    monkeypatch.delenv("NOVA_GATE_LIVE_PROVIDER_INSTANCES", raising=False)
    count, _source = gate._live_provider_instance_count()
    assert count == 1


def test_a_warning_after_the_latest_reload_still_counts(gate, monkeypatch, tmp_path):
    log = tmp_path / "gateway.log"
    log.write_text("===== gateway boot 2026-11-01T00:00:00Z (pid 1) =====\nINFO:     Started server process [7533]\nmore than one AioSandboxProvider instance in this process (2) - races\n")
    monkeypatch.setenv("NOVA_GATE_GATEWAY_LOG", str(log))
    monkeypatch.delenv("NOVA_GATE_LIVE_PROVIDER_INSTANCES", raising=False)
    count, _source = gate._live_provider_instance_count()
    assert count == 2


def test_a_reload_line_alone_is_a_boundary(gate, monkeypatch, tmp_path):
    log = tmp_path / "gateway.log"
    log.write_text("INFO:     Started server process [9]\nall quiet\n")
    monkeypatch.setenv("NOVA_GATE_GATEWAY_LOG", str(log))
    monkeypatch.delenv("NOVA_GATE_LIVE_PROVIDER_INSTANCES", raising=False)
    count, _source = gate._live_provider_instance_count()
    assert count == 1


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def _gates_daemon():
    script = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "gates-daemon.py"
    spec = importlib.util.spec_from_file_location("gates_daemon_runs_test", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gates_daemon_runs_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_runs_producer_is_registered():
    daemon = _gates_daemon()
    producers = {p.name: p for p in daemon.build_producers()}
    assert "runs" in producers, "the runs gate has no producer — nothing would write runs.json"
    producer = producers["runs"]
    assert producer.argv == ["scripts/gates/runs-gate.py"]
    assert producer.interval_sec == 300
    assert producer.kind == "gate"


def test_the_runs_producer_interval_is_env_overridable(monkeypatch):
    daemon = _gates_daemon()
    monkeypatch.setenv("NOVA_GATE_RUNS_INTERVAL", "42")
    producers = {p.name: p for p in daemon.build_producers()}
    assert producers["runs"].interval_sec == 42


def test_the_watchdog_registers_a_runs_probe():
    script = Path(__file__).resolve().parents[2] / "scripts" / "healthcheck-daemon.py"
    spec = importlib.util.spec_from_file_location("healthcheck_runs_test", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["healthcheck_runs_test"] = mod
    spec.loader.exec_module(mod)
    names = [name for name, _factory in mod.build_probe_factories()]
    assert "P17_runs" in names
    assert len(names) == len(set(names)), "duplicate probe name"


def test_the_watchdog_probe_escalates_the_gate_overall(monkeypatch, tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "healthcheck-daemon.py"
    spec = importlib.util.spec_from_file_location("healthcheck_runs_probe_test", script)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["healthcheck_runs_probe_test"] = mod
    spec.loader.exec_module(mod)

    gate_file = tmp_path / "runs.json"
    monkeypatch.setenv("NOVA_RUNS_GATE_STATUS_PATH", str(gate_file))

    # No file yet -> yellow, never red.
    assert mod.asyncio.run(mod.probe_runs()).status is mod.Status.YELLOW

    # A stale file -> yellow, never red.
    gate_file.write_text(
        json.dumps(
            {
                "gate": "runs",
                "checked_at_epoch": time.time() - 10_000,
                "overall": "red",
                "checks": [{"name": "stuck_runs", "status": "red", "detail": "x"}],
            }
        )
    )
    stale = mod.asyncio.run(mod.probe_runs())
    assert stale.status is mod.Status.YELLOW
    assert "stale" in stale.detail

    # A fresh red gate -> red.
    gate_file.write_text(
        json.dumps(
            {
                "gate": "runs",
                "checked_at_epoch": time.time(),
                "overall": "red",
                "checks": [{"name": "stuck_runs", "status": "red", "detail": "x"}],
            }
        )
    )
    fresh = mod.asyncio.run(mod.probe_runs())
    assert fresh.status is mod.Status.RED
    assert "stuck_runs" in fresh.detail

    # A fresh green gate -> green.
    gate_file.write_text(
        json.dumps(
            {
                "gate": "runs",
                "checked_at_epoch": time.time(),
                "overall": "green",
                "checks": [{"name": "stuck_runs", "status": "green", "detail": "ok"}],
            }
        )
    )
    assert mod.asyncio.run(mod.probe_runs()).status is mod.Status.GREEN
