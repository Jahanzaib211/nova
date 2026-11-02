"""Postgres connection checks of scripts/gates/runs-gate.py.

On 2026-11-01 a connection leak held max_connections (100) for ~10 h and no
gate counted connections. These drive ``connection_saturation`` and
``idle_in_transaction`` through a fake ``docker exec psql``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "runs-gate.py"
_SEP = "\x1f"


@pytest.fixture
def gate():
    spec = importlib.util.spec_from_file_location("runs_gate_conn_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["runs_gate_conn_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_psql(gate, monkeypatch, *, stdout="", stderr="", returncode=0):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return gate.subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(gate, "_PG_UNAVAILABLE", None)
    monkeypatch.setattr(gate, "_PG_SATURATED", None)
    return calls


def _conn_rows(max_conn, *clients):
    """CONNECTIONS_QUERY output: the settings repeated on every (addr, app, state, n) row."""
    return "".join(_SEP.join([str(max_conn), "3", addr, app, state, str(n)]) + "\n" for addr, app, state, n in clients)


def _idle_rows(*sessions):
    return "".join(_SEP.join([str(pid), addr, app, str(age), "UPDATE runs SET x"]) + "\n" for pid, addr, app, age in sessions)


@pytest.mark.parametrize(
    ("used", "status"),
    [(15, "green"), (59, "green"), (60, "yellow"), (84, "yellow"), (85, "red"), (100, "red")],
)
def test_connection_saturation_bands(gate, monkeypatch, used, status):
    _fake_psql(gate, monkeypatch, stdout=_conn_rows(100, ("192.168.200.3", "gateway", "idle", used - 1), ("local", "psql", "active", 1)))
    result = gate.check_connection_saturation(gate.PostgresDialect())
    assert result["status"] == status
    assert result["current"] == used
    assert result["max_connections"] == 100


def test_connection_saturation_names_the_busiest_client_first(gate, monkeypatch):
    _fake_psql(
        gate,
        monkeypatch,
        stdout=_conn_rows(100, ("10.0.0.9", "worker", "idle", 3), ("10.0.0.5", "gateway", "idle", 70), ("10.0.0.5", "gateway", "active", 2)),
    )
    result = gate.check_connection_saturation(gate.PostgresDialect())
    assert result["status"] == gate.YELLOW
    assert result["clients"][0]["client_addr"] == "10.0.0.5"
    assert result["clients"][0]["connections"] == 72
    assert result["clients"][0]["states"] == {"idle": 70, "active": 2}
    assert result["detail"].index("10.0.0.5=72") < result["detail"].index("10.0.0.9=3")


def test_connection_saturation_thresholds_are_env_overridable(gate, monkeypatch):
    monkeypatch.setenv("NOVA_GATE_DB_CONN_WARN_PCT", "10")
    monkeypatch.setenv("NOVA_GATE_DB_CONN_RED_PCT", "14")
    _fake_psql(gate, monkeypatch, stdout=_conn_rows(100, ("local", "psql", "active", 15)))
    assert gate.check_connection_saturation(gate.PostgresDialect())["status"] == gate.RED


@pytest.mark.parametrize(
    "stderr",
    [
        "psql: error: connection to server on socket failed: FATAL:  sorry, too many clients already",
        "psql: error: FATAL:  remaining connection slots are reserved for roles with the SUPERUSER attribute",
    ],
)
def test_a_refused_connection_is_red_saturation_not_unknown(gate, monkeypatch, stderr):
    """The 2026-11-01 state: the gate itself cannot connect -- there are no slots."""
    _fake_psql(gate, monkeypatch, stderr=stderr, returncode=2)
    result = gate.check_connection_saturation(gate.PostgresDialect())
    assert result["status"] == gate.RED
    assert result["refused"] is True
    assert "exhausted" in result["detail"]
    # The other checks say why they could not run, not a bare "could not query".
    other = gate.check_stuck_runs(gate.PostgresDialect())
    assert other["status"] == gate.YELLOW
    assert "no connection slot" in other["detail"]


def test_connection_saturation_is_yellow_when_postgres_is_unreachable(gate, monkeypatch):
    _fake_psql(gate, monkeypatch, stderr="Error response from daemon: container is not running", returncode=1)
    assert gate.check_connection_saturation(gate.PostgresDialect())["status"] == gate.YELLOW


def test_connection_checks_are_not_applicable_on_sqlite(gate):
    assert gate.check_connection_saturation(gate.SqliteDialect())["status"] == gate.GREEN
    assert gate.check_idle_in_transaction(gate.SqliteDialect())["status"] == gate.GREEN


def test_connection_queries_are_read_only_selects(gate):
    for query in (gate.CONNECTIONS_QUERY, gate.idle_in_transaction_query(300)):
        assert query.lstrip().upper().startswith("SELECT ")
        assert "pg_stat_activity" in query
        assert "client backend" in query
    assert "300 seconds" in gate.idle_in_transaction_query(300)


def test_idle_in_transaction_green_when_none(gate, monkeypatch):
    _fake_psql(gate, monkeypatch, stdout="")
    result = gate.check_idle_in_transaction(gate.PostgresDialect())
    assert result["status"] == gate.GREEN
    assert result["count"] == 0


def test_idle_in_transaction_yellow_then_red_with_age(gate, monkeypatch):
    _fake_psql(gate, monkeypatch, stdout=_idle_rows((101, "10.0.0.5", "gateway", 600)))
    yellow = gate.check_idle_in_transaction(gate.PostgresDialect())
    assert yellow["status"] == gate.YELLOW
    assert "pid 101" in yellow["detail"]

    _fake_psql(gate, monkeypatch, stdout=_idle_rows((101, "10.0.0.5", "gateway", 600), (102, "10.0.0.5", "gateway", 7200)))
    red = gate.check_idle_in_transaction(gate.PostgresDialect())
    assert red["status"] == gate.RED
    assert red["count"] == 2
    assert red["oldest_sec"] == 7200


def test_idle_in_transaction_ages_are_env_overridable(gate, monkeypatch):
    monkeypatch.setenv("NOVA_GATE_DB_IDLE_TX_SECONDS", "60")
    monkeypatch.setenv("NOVA_GATE_DB_IDLE_TX_RED_SECONDS", "120")
    calls = _fake_psql(gate, monkeypatch, stdout=_idle_rows((7, "local", "psql", 130)))
    assert gate.check_idle_in_transaction(gate.PostgresDialect())["status"] == gate.RED
    assert "60 seconds" in " ".join(calls[0])


def test_connection_checks_are_registered_and_reach_the_watchdog(gate):
    names = {fn.__name__ for fn in gate.CHECKS}
    assert {"check_connection_saturation", "check_idle_in_transaction"} <= names
    # P17_runs escalates runs.json's overall, so a red saturation pages.
    assert gate.PSQL_TIMEOUT_S * gate.MAX_QUERIES < 60
