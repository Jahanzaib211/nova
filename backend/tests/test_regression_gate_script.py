"""Unit tests for scripts/gates/regression-gate.py (regression.json producer)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "regression-gate.py"
spec = importlib.util.spec_from_file_location("regression_gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

CFG = """
speech:
  enabled: true
jobs:
  enabled: true
runtimes:
  enabled: true
sandbox:
  use: x
  image: nova-sandbox-android:latest
"""


def test_expected_overlays_follow_speech_and_acp_flags(monkeypatch):
    monkeypatch.setenv("NOVA_ACP_AGENTS", "1")
    exp = gate.expected_overlays(CFG)
    assert {"docker-compose.voice.yaml", "docker-compose.cli-auth.yaml", "docker-compose.acp.yaml"} <= exp
    monkeypatch.setenv("NOVA_ACP_AGENTS", "0")
    assert "docker-compose.acp.yaml" not in gate.expected_overlays(CFG)


def test_overlays_missing_is_red_and_empty_is_red():
    r = gate.evaluate_overlays({"a.yaml", "b.yaml"}, {"a.yaml"})
    assert r["status"] == "red" and r["missing"] == ["b.yaml"]
    assert gate.evaluate_overlays({"a.yaml"}, set())["status"] == "red"
    assert gate.evaluate_overlays({"a.yaml"}, {"a.yaml", "extra.yaml"})["status"] == "green"


def test_sandbox_image_missing_is_red_the_2026_09_20_case():
    r = gate.evaluate_sandbox_image("nova-sandbox-android:latest", False, None)
    assert r["status"] == "red" and "make sandbox-image" in r["detail"]
    assert gate.evaluate_sandbox_image("x", True, None)["status"] == "yellow"
    assert gate.evaluate_sandbox_image("x", True, {"layer": "android", "build_id": "b1"})["status"] == "green"
    assert gate.evaluate_sandbox_image(None, False, None)["status"] == "red"


def test_sandbox_tools_ignore_android_group_on_lower_rungs():
    missing = {"languages": [], "android": ["sdkmanager", "gradle", "kotlinc"], "network": []}
    assert gate.evaluate_sandbox_tools(True, "tools", missing)["status"] == "green"
    assert gate.evaluate_sandbox_tools(True, "android", missing)["status"] == "yellow"
    assert gate.evaluate_sandbox_tools(False, None, {})["status"] == "red"
    r = gate.evaluate_sandbox_tools(True, "android", {"webapp": ["sqlmap"]})
    assert "sqlmap" in r["detail"]


def test_backend_extras_red_names_the_stripped_extra():
    r = gate.evaluate_backend_extras({"postgres": [], "voice": ["faster_whisper"], "trading": []})
    assert r["status"] == "red" and "voice: faster_whisper" in r["detail"]
    assert gate.evaluate_backend_extras({"postgres": [], "voice": [], "trading": []})["status"] == "green"
    assert gate.evaluate_backend_extras({"_": ["gateway container unreachable"]})["status"] == "yellow"


def test_config_prereqs_check_each_enabled_section(monkeypatch):
    monkeypatch.setenv("NOVA_ACP_AGENTS", "1")
    ok = {"voice_weights": True, "jobs_worker": True, "acp_mounts": True, "acp_ready": True}
    assert gate.evaluate_config_prereqs(CFG, ok)["status"] == "green"
    r = gate.evaluate_config_prereqs(CFG, {**ok, "acp_ready": False, "voice_weights": False})
    assert r["status"] == "red" and len(r["problems"]) == 2


def test_services_red_on_offline_pm2_yellow_on_stale_frontend():
    pm2 = {a: "online" for a in gate.NOVA_PM2_APPS}
    ep = {"gateway /health": 200}
    assert gate.evaluate_services(pm2, ep, "b1", "b1")["status"] == "green"
    assert gate.evaluate_services(pm2, ep, "b1", "b2")["status"] == "yellow"
    assert gate.evaluate_services({**pm2, "nova-gates": "errored"}, ep, "b1", "b1")["status"] == "red"
    assert gate.evaluate_services(pm2, {"public": None}, "b1", "b1")["status"] == "red"


def test_services_skip_what_the_watchdog_has_disabled():
    """2026-10-29: nova-litellm commented out of ecosystem.config.js and
    P10_litellm disabled — the gate must not stay RED on its absence."""
    pm2 = {a: "online" for a in gate.NOVA_PM2_APPS if a != "nova-litellm"}
    ep = {"public": 200, "litellm :4000": None}
    assert gate.evaluate_services(pm2, ep, "b1", "b1")["status"] == "red"
    r = gate.evaluate_services(pm2, ep, "b1", "b1", disabled={"P10_litellm"})
    assert r["status"] == "green", r["detail"]
    assert "nova-litellm" in r["skipped"] and "litellm :4000" not in r["endpoints"]


def test_services_tunnel_is_checked_by_connector_not_pm2_name():
    pm2 = {a: "online" for a in gate.NOVA_PM2_APPS}
    ep = {"public": 200}
    assert "tunnel-nova" not in gate.NOVA_PM2_APPS
    assert gate.evaluate_services(pm2, ep, "b1", "b1", tunnel=(True, "systemd cloudflared.service active"))["status"] == "green"
    r = gate.evaluate_services(pm2, ep, "b1", "b1", tunnel=(False, "no connector"))
    assert r["status"] == "red" and "tunnel connector down" in r["detail"]
    assert gate.evaluate_services(pm2, ep, "b1", "b1", disabled={"P12_tunnel"}, tunnel=(False, "x"))["status"] == "green"


def test_gate_reads_disabled_probes_from_ecosystem_when_env_unset(monkeypatch, tmp_path):
    eco = tmp_path / "ecosystem.config.js"
    eco.write_text('module.exports = { apps: [ { name: "w", env: {\n  // HEALTHCHECK_DISABLED_PROBES: "P1_nginx",\n  HEALTHCHECK_DISABLED_PROBES:\n    "P9_bridge,P10_litellm",\n} } ] };\n')
    monkeypatch.setenv("NOVA_ECOSYSTEM_FILE", str(eco))
    monkeypatch.delenv("HEALTHCHECK_DISABLED_PROBES", raising=False)
    assert gate.gate_disabled_probes() == {"P9_bridge", "P10_litellm"}
    monkeypatch.setenv("HEALTHCHECK_DISABLED_PROBES", "P12_tunnel")
    assert gate.gate_disabled_probes() == {"P12_tunnel"}


def test_tunnel_connector_prefers_cloudflared_ready_endpoint(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_TUNNEL_METRICS_URLS", "http://127.0.0.1:20241/ready")
    calls: list = []
    monkeypatch.setattr(gate, "http_status", lambda url, timeout=5.0, headers=None: 200)
    monkeypatch.setattr(gate, "run", lambda cmd, timeout=60: calls.append(cmd) or (3, ""))
    ok, via = gate.tunnel_connector()
    assert ok and "ready" in via and calls == []
    # Metrics unreachable: falls back to the configured systemd units.
    monkeypatch.setattr(gate, "http_status", lambda url, timeout=5.0, headers=None: None)
    monkeypatch.setenv("HEALTHCHECK_TUNNEL_UNITS", "my-tunnel.service")
    monkeypatch.setattr(gate, "run", lambda cmd, timeout=60: (0, "active") if cmd[:2] == ["systemctl", "is-active"] and cmd[2] == "my-tunnel.service" else (1, ""))
    assert gate.tunnel_connector() == (True, "systemd my-tunnel.service active")


def test_counts_never_go_below_baseline():
    base = {"skills": 28, "capability_ops": 33}
    assert gate.evaluate_counts({"skills": 28, "capability_ops": 36}, base)["status"] == "green"
    r = gate.evaluate_counts({"skills": 20, "capability_ops": 36}, base)
    assert r["status"] == "red" and "skills 28->20" in r["detail"]
    assert gate.evaluate_counts({"skills": 1}, None)["status"] == "yellow"


def test_report_overall_and_document_shape():
    rep = gate.build_report([gate.check("a", "green", "ok"), gate.check("b", "yellow", "meh")])
    assert rep["gate"] == "regression" and rep["overall"] == "yellow" and rep["ok"] is True
    assert gate.build_report([gate.check("a", "red", "x")])["ok"] is False
