"""Unit tests for scripts/gates/jobs-gate.py (jobrunner.json producer)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "jobs-gate.py"
spec = importlib.util.spec_from_file_location("jobs_gate", SCRIPT)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_green_with_live_worker_and_empty_queues():
    report = gate.build_report({"queued": 0, "running": 1, "retrying": 0, "dead_letter": 0, "oldest_queued_age_s": None, "workers": [{"last_seen_age_s": 3}]})
    assert report["gate"] == "jobrunner" and report["overall"] == "green" and report["ok"] is True
    assert [c["name"] for c in report["checks"]] == ["workers", "backlog", "dead_letter"]


def test_red_without_live_worker():
    report = gate.build_report({"queued": 2, "workers": [{"last_seen_age_s": 9999}]})
    assert report["overall"] == "red"
    assert report["checks"][0]["detail"].startswith("no worker has heartbeated")


def test_yellow_on_backlog_or_dead_letter():
    big = gate.build_report({"queued": gate.QUEUE_WARN, "workers": [{"last_seen_age_s": 1}]})
    assert big["overall"] == "yellow"
    old = gate.build_report({"queued": 1, "oldest_queued_age_s": gate.OLDEST_QUEUED_WARN_S + 1, "workers": [{"last_seen_age_s": 1}]})
    assert old["overall"] == "yellow"
    dead = gate.build_report({"queued": 0, "dead_letter": 3, "workers": [{"last_seen_age_s": 1}]})
    assert dead["overall"] == "yellow" and "3 dead-lettered" in dead["checks"][2]["detail"]


def test_red_when_summary_unavailable():
    report = gate.build_report(None, error="connection refused")
    assert report["overall"] == "red" and "connection refused" in report["checks"][0]["detail"]


def test_main_print_only_and_no_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "ops_token", lambda: "")
    rc = gate.main(["--print", "--json", "--status-path", str(tmp_path / "j.json")])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["checks"][0]["detail"].startswith("summary unavailable: NOVA_OPS_TOKEN")
    assert not (tmp_path / "j.json").exists()


def test_write_atomic(tmp_path):
    target = tmp_path / "gates" / "jobrunner.json"
    gate.write_atomic(target, {"gate": "jobrunner"})
    assert json.loads(target.read_text())["gate"] == "jobrunner"
    assert [p.name for p in target.parent.iterdir()] == ["jobrunner.json"]
