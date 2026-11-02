"""The gates tell the truth: optional stacks, the sandbox rebuild cache, disk
and docker growth, and the disk watchdog probe.

2026-11-02: inventory sat red for stacks this host never deployed (Mailcow,
Chatwoot, Twenty, OpenClaw), capabilities yellow for the same three,
sandbox-health red for a missing *rebuild cache* while the sandbox image chain
was intact -- and log rotation was leaking an 80 MB copy per hour while the
disk check stayed green. A gate that is red every cycle is a gate nobody
reads; a gate that only checks a percentage cannot see a disk filling.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _REPO / rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


opt = _load("scripts/lib/gate_optional.py", "gate_optional_truth_test")
inventory = _load("scripts/inventory.py", "inventory_truth_test")
capabilities = _load("scripts/gates/capabilities-gate.py", "capabilities_truth_test")
sandbox = _load("scripts/gates/sandbox-health-gate.py", "sandbox_health_truth_test")
host = _load("scripts/gates/host-gate.py", "host_gate_truth_test")
healthcheck = _load("scripts/healthcheck-daemon.py", "healthcheck_truth_test")


# --------------------------------------------------------------------------- optional stacks


def _item(ident, health, detail="probe"):
    return inventory.item(ident, kind="mail" if ident.startswith("mailcow") else "crm", display_name=ident, health=health, detail=detail)


def test_optional_stacks_parse_env_and_env_file(tmp_path):
    assert opt.parse_stacks(" Mailcow, chatwoot twenty,,") == {"mailcow", "chatwoot", "twenty"}
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=1\nNOVA_GATE_OPTIONAL_STACKS=mailcow,openclaw  # not deployed here\n")
    assert opt.optional_stacks(environ={}, env_file=env_file) == {"mailcow", "openclaw"}
    # The environment wins, and an explicit empty value switches the feature off.
    assert opt.optional_stacks(environ={"NOVA_GATE_OPTIONAL_STACKS": "twenty"}, env_file=env_file) == {"twenty"}
    assert opt.optional_stacks(environ={"NOVA_GATE_OPTIONAL_STACKS": ""}, env_file=env_file) == frozenset()


def test_stack_membership_is_by_id_prefix():
    stacks = {"mailcow", "openclaw"}
    assert opt.stack_of("mailcow_smtp", stacks) == "mailcow"
    assert opt.stack_of("openclaw", stacks) == "openclaw"
    assert opt.stack_of("openclaw_cli", stacks) == "openclaw"
    assert opt.stack_of("mailcowish", stacks) is None
    assert opt.stack_of("nova_gateway", stacks) is None


def test_a_not_deployed_optional_stack_is_disabled_not_red():
    checks = [_item("mailcow_smtp", "down"), _item("mailcow_imap", "down"), _item("mailcow_api", "down"), _item("twenty", "down")]
    out = opt.apply_to_inventory(checks, {"mailcow"})
    for entry in out[:3]:
        assert entry["status"] == "green"
        assert entry["health"] == "disabled"
        assert entry["probed_health"] == "down"
        assert "NOVA_GATE_OPTIONAL_STACKS" in entry["detail"]
    # Not declared: still red.
    assert out[3]["status"] == "red"
    # Input untouched.
    assert checks[0]["status"] == "red"


def test_a_partly_running_optional_stack_keeps_its_real_failure():
    """Something of the stack answers, so it is meant to run: never hide the rest."""
    checks = [_item("mailcow_smtp", "healthy"), _item("mailcow_imap", "down")]
    out = opt.apply_to_inventory(checks, {"mailcow"})
    assert [c["status"] for c in out] == ["green", "red"]
    assert out[1]["health"] == "down"


def test_inventory_report_applies_the_declaration(monkeypatch):
    monkeypatch.setenv("NOVA_GATE_OPTIONAL_STACKS", "chatwoot,openclaw")
    probes = [("business", lambda: [_item("chatwoot", "down"), _item("openclaw", "down"), _item("twenty", "down")]), ("cli", lambda: [_item("openclaw_cli", "down")])]
    report = inventory.build_report(probes)
    status = {c["name"]: c["status"] for c in report["checks"]}
    assert status == {"chatwoot": "green", "openclaw": "green", "twenty": "red", "openclaw_cli": "green"}
    assert report["overall"] == "red"
    monkeypatch.setenv("NOVA_GATE_OPTIONAL_STACKS", "chatwoot,openclaw,twenty")
    assert inventory.build_report(probes)["overall"] == "green"


def test_inventory_without_a_declaration_is_unchanged(monkeypatch):
    monkeypatch.setenv("NOVA_GATE_OPTIONAL_STACKS", "")
    report = inventory.build_report([("business", lambda: [_item("chatwoot", "down")])])
    assert report["overall"] == "red"


# --------------------------------------------------------------------------- capabilities


def _ops(detail, healthy=False):
    return {"status": {"integrations": {"configured": True, "healthy": healthy, "detail": detail}, "jobs": {"configured": True, "healthy": True, "detail": "ok"}}}


def test_capabilities_excuses_only_declared_optional_failures():
    ops = _ops("37/41 healthy, 3 required failing: chatwoot, openclaw, twenty")
    assert capabilities.evaluate_modules(ops)["status"] == "yellow"
    excused = capabilities.evaluate_modules(ops, frozenset({"chatwoot", "openclaw", "twenty"}))
    assert excused["status"] == "green"
    assert excused["optional"] == {"integrations": ["chatwoot", "openclaw", "twenty"]}
    assert "not deployed" in excused["detail"]


def test_capabilities_stays_yellow_when_anything_else_fails():
    ops = _ops("37/41 healthy, 3 required failing: chatwoot, openclaw, searxng")
    result = capabilities.evaluate_modules(ops, frozenset({"chatwoot", "openclaw", "twenty"}))
    assert result["status"] == "yellow"
    assert result["unhealthy"] == ["integrations"]


@pytest.mark.parametrize(
    "detail",
    [
        "3 required failing: chatwoot, openclaw",  # count does not add up: truncated by the gateway
        "integration registry unavailable",  # no list at all
    ],
)
def test_capabilities_never_assumes_optional_from_an_incomplete_detail(detail):
    result = capabilities.evaluate_modules(_ops(detail), frozenset({"chatwoot", "openclaw", "twenty"}))
    assert result["status"] == "yellow"


# --------------------------------------------------------------------------- sandbox rebuild cache


def _vendor(tmp_path, *files):
    vendor = tmp_path / "docker" / "sandbox" / "vendor"
    vendor.mkdir(parents=True)
    for f in files:
        (vendor / f).write_bytes(b"x")
    return tmp_path / "docker" / "sandbox"


def test_incomplete_vendor_cache_is_yellow_while_the_image_chain_is_intact(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "SANDBOX_DIR", _vendor(tmp_path, "go.tar", "uv"))
    result = sandbox.evaluate_vendor_integrity(images_present=True)
    assert result["status"] == "yellow"
    assert result["offline_rebuild"] is False
    assert "rust.tar" in result["missing"]
    assert "NOVA_ANDROID_DONOR" in result["detail"]


def test_incomplete_vendor_cache_is_red_when_the_image_chain_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "SANDBOX_DIR", _vendor(tmp_path, "go.tar"))
    assert sandbox.evaluate_vendor_integrity(images_present=False)["status"] == "red"
    # The default stays the conservative one for any other caller.
    assert sandbox.evaluate_vendor_integrity()["status"] == "red"


def test_auto_rebuild_still_sees_an_incomplete_cache():
    # vendor_ok feeds auto_rebuild; a missing image + incomplete cache must not
    # claim a rebuild can proceed offline.
    assert sandbox.evaluate_auto_rebuild(image_ok=False, vendor_ok=False, rebuild=False)["status"] == "yellow"
    assert "manual rebuild" in sandbox.evaluate_auto_rebuild(image_ok=False, vendor_ok=False, rebuild=False)["detail"]


# --------------------------------------------------------------------------- disk + docker growth


@pytest.mark.parametrize(("text", "expected"), [("37.57GB", 37_570_000_000), ("2.509kB", 2509), ("0B", 0), ("114MB", 114_000_000), ("1.2TB", 1_200_000_000_000), ("", 0), ("N/A", 0)])
def test_docker_sizes_parse_as_si_units(text, expected):
    assert host.parse_docker_size(text) == expected


def _df(cache_gb=(), volumes=()):
    return {
        "BuildCache": [{"Size": f"{gb}GB", "InUse": "false"} for gb in cache_gb],
        "Volumes": [{"Name": n, "Size": f"{gb}GB"} for n, gb in volumes],
    }


@pytest.mark.parametrize(("cache", "status"), [((10, 20), "green"), ((30, 25), "yellow"), ((60, 45), "red")])
def test_build_cache_bands(cache, status):
    result = host.evaluate_build_cache(_df(cache_gb=cache), 50, 100)
    assert result["status"] == status
    assert result["entries"] == len(cache)
    if status != "green":
        assert "docker builder prune" in result["detail"]


def test_volume_check_names_the_largest_volume():
    df = _df(volumes=[("nova_ollama-models", 2), ("nova_nova-postgres-data", 70), ("nova_gateway-venv", 5)])
    result = host.evaluate_volumes(df, 50, 100)
    assert result["status"] == "yellow"
    assert result["largest"][0]["name"] == "nova_nova-postgres-data"
    assert "nova_nova-postgres-data is over 50 GB" in result["detail"]
    assert host.evaluate_volumes(_df(), 50, 100)["status"] == "green"


def test_docker_checks_are_yellow_when_docker_cannot_answer(monkeypatch):
    monkeypatch.setattr(host, "_docker_df_verbose", lambda: (None, "docker unavailable: boom"))
    assert host.check_docker_build_cache()["status"] == "yellow"
    assert host.check_docker_volumes()["status"] == "yellow"


def test_disk_eta_needs_an_hour_of_history():
    now = time.time()
    assert host.disk_eta([(now - 60, 100)], now, 200, 1000) == (None, None)
    rate, eta = host.disk_eta([(now - 7200, 100 * 2**30)], now, 102 * 2**30, 10 * 2**30)
    assert rate == pytest.approx(2**30)
    assert eta == pytest.approx(10)
    # Shrinking or flat: no ETA.
    assert host.disk_eta([(now - 7200, 200)], now, 100, 1000)[1] is None


def test_disk_growth_turns_red_when_full_within_a_day(tmp_path, monkeypatch):
    history = tmp_path / "disk-history.json"
    monkeypatch.setenv("NOVA_GATE_DISK_HISTORY_PATH", str(history))
    usage = type("U", (), {"total": 100 * 2**30, "used": 90 * 2**30, "free": 10 * 2**30})
    monkeypatch.setattr(host.shutil, "disk_usage", lambda _p: usage)
    # 2 GiB/hour for the last two hours -> 10 GiB free is 5 h away.
    history.write_text(json.dumps([[time.time() - 7200, 86 * 2**30]]))
    result = host.check_disk_growth(record=False)
    assert result["status"] == "red"
    assert result["hours_to_full"] == pytest.approx(5, rel=0.01)
    assert len(json.loads(history.read_text())) == 1, "--print must not append to the history"
    host.check_disk_growth(record=True)
    assert len(json.loads(history.read_text())) == 2


def test_disk_thresholds_are_env_overridable(monkeypatch):
    usage = type("U", (), {"total": 100, "used": 50, "free": 50})
    monkeypatch.setattr(host.shutil, "disk_usage", lambda _p: usage)
    assert host.check_disk()["status"] == "green"
    monkeypatch.setenv("NOVA_GATE_DISK_WARN_PCT", "40")
    assert host.check_disk()["status"] == "yellow"


def test_host_gate_registers_the_growth_checks():
    names = {fn.__name__ for fn in host.CHECKS}
    assert {"check_disk", "check_disk_growth", "check_docker_build_cache", "check_docker_volumes"} <= names


# --------------------------------------------------------------------------- P18_disk watchdog probe


def _host_file(tmp_path, monkeypatch, checks, *, age=0):
    path = tmp_path / "host.json"
    path.write_text(json.dumps({"gate": "host", "checked_at_epoch": time.time() - age, "overall": "red", "producer_interval_sec": 300, "checks": checks}))
    monkeypatch.setenv("NOVA_HOST_GATE_STATUS_PATH", str(path))


def test_disk_probe_is_registered_without_a_repair():
    names = [name for name, _ in healthcheck.build_probe_factories()]
    assert "P18_disk" in names and "P17_runs" in names
    assert len(names) == len(set(names))
    # Read-only escalations: nothing may "fix" a full disk or the runs table.
    assert "P18_disk" not in healthcheck.FIX_DISPATCH
    assert "P17_runs" not in healthcheck.FIX_DISPATCH


def test_disk_probe_reads_only_the_disk_checks(tmp_path, monkeypatch):
    _host_file(tmp_path, monkeypatch, [{"name": "disk", "status": "green"}, {"name": "rotate_job", "status": "red", "detail": "failed"}])
    assert asyncio.run(healthcheck.probe_disk()).status is healthcheck.Status.GREEN

    _host_file(tmp_path, monkeypatch, [{"name": "disk_growth", "status": "red", "detail": "full in 0.2 days"}, {"name": "rotate_job", "status": "green"}])
    red = asyncio.run(healthcheck.probe_disk())
    assert red.status is healthcheck.Status.RED
    assert "disk_growth" in red.detail

    _host_file(tmp_path, monkeypatch, [{"name": "docker_build_cache", "status": "yellow"}])
    assert asyncio.run(healthcheck.probe_disk()).status is healthcheck.Status.YELLOW


def test_disk_probe_is_yellow_for_a_missing_or_stale_file(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVA_HOST_GATE_STATUS_PATH", str(tmp_path / "absent.json"))
    assert asyncio.run(healthcheck.probe_disk()).status is healthcheck.Status.YELLOW
    _host_file(tmp_path, monkeypatch, [{"name": "disk", "status": "red"}], age=10_000)
    stale = asyncio.run(healthcheck.probe_disk())
    assert stale.status is healthcheck.Status.YELLOW
    assert "stale" in stale.detail
