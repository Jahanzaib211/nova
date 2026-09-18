"""Unit tests for scripts/inventory.py (host-side machine inventory producer).

Run with:
    cd backend && PYTHONPATH=../scripts uv run pytest tests/test_inventory_script.py -v

The script is stdlib-only and imports nothing from the backend. These tests
load it by path, the same way ``test_healthcheck_daemon.py`` does, and pin its
status vocabulary to ``contracts/integrations_health_contract.json``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory.py"
CONTRACT_PATH = _REPO_ROOT / "contracts" / "integrations_health_contract.json"

spec = importlib.util.spec_from_file_location("nova_inventory", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_status_vocabulary_matches_contract(contract):
    assert set(inventory.HEALTH_TO_GATE) == set(contract["statuses"])
    assert set(inventory.KINDS) == set(contract["kinds"])


def test_item_has_contract_shape_plus_gate_fields(contract):
    item = inventory.item(
        "ollama",
        kind="llm_gateway",
        display_name="Ollama",
        health="healthy",
        detail="3 models",
        endpoint="http://127.0.0.1:11434",
        latency_ms=4.2,
        capabilities=["chat"],
    )
    for key in contract["item_schema"]["properties"]:
        assert key in item, key
    # Gate/console fields on top of the contract shape.
    assert item["name"] == "ollama"
    assert item["status"] == inventory.GREEN
    assert item["health"] == "healthy"
    assert isinstance(item["checked_at"], str)


@pytest.mark.parametrize(
    ("health", "gate"),
    [
        ("healthy", "green"),
        ("disabled", "green"),
        ("degraded", "yellow"),
        ("unknown", "yellow"),
        ("down", "red"),
    ],
)
def test_health_to_gate_mapping(health, gate):
    assert inventory.HEALTH_TO_GATE[health] == gate


def test_item_rejects_unknown_kind_or_health():
    with pytest.raises(ValueError):
        inventory.item("x", kind="toaster", display_name="x", health="healthy", detail="")
    with pytest.raises(ValueError):
        inventory.item("x", kind="mail", display_name="x", health="on-fire", detail="")


def test_parse_docker_ps_maps_container_names_to_state():
    lines = "\n".join(
        [
            json.dumps({"Names": "deer-flow-searxng", "State": "running", "Status": "Up 3 days (healthy)", "Image": "searxng/searxng"}),
            json.dumps({"Names": "deer-flow-crawl4ai", "State": "exited", "Status": "Exited (1) 2 hours ago", "Image": "unclecode/crawl4ai"}),
        ]
    )
    parsed = inventory.parse_docker_ps(lines)
    assert parsed["deer-flow-searxng"]["state"] == "running"
    assert parsed["deer-flow-searxng"]["healthy"] is True
    assert parsed["deer-flow-crawl4ai"]["state"] == "exited"
    assert parsed["deer-flow-crawl4ai"]["healthy"] is False


def test_parse_docker_ps_tolerates_garbage():
    assert inventory.parse_docker_ps("not json\n\n") == {}


def test_container_health_from_docker_state():
    assert inventory.container_health({"state": "running", "healthy": True}) == "healthy"
    assert inventory.container_health({"state": "running", "healthy": None}) == "healthy"
    assert inventory.container_health({"state": "running", "healthy": False}) == "degraded"
    assert inventory.container_health({"state": "exited", "healthy": False}) == "down"
    assert inventory.container_health(None) == "down"


def test_parse_pm2_jlist_extracts_status_and_restarts():
    payload = json.dumps(
        [
            {"name": "nova", "pm2_env": {"status": "online", "restart_time": 3}},
            {"name": "tunnel-whmcs", "pm2_env": {"status": "stopped", "restart_time": 0}},
        ]
    )
    parsed = inventory.parse_pm2_jlist(payload)
    assert parsed["nova"] == {"status": "online", "restarts": 3}
    assert parsed["tunnel-whmcs"]["status"] == "stopped"
    assert inventory.parse_pm2_jlist("nope") == {}


def test_parse_meminfo():
    text = "MemTotal:       31000000 kB\nMemAvailable:   11000000 kB\nSwapTotal:      15000000 kB\nSwapFree:        5000000 kB\n"
    mem = inventory.parse_meminfo(text)
    assert mem["mem_total_kb"] == 31_000_000
    assert mem["mem_available_kb"] == 11_000_000
    assert mem["swap_used_kb"] == 10_000_000


def test_parse_extensions_config_lists_mcp_and_skills(tmp_path):
    cfg = {
        "mcpServers": {
            "github": {"enabled": False, "type": "stdio", "command": "npx", "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "secret"}},
            "docs": {"enabled": True, "type": "http", "url": "http://x/mcp", "headers": {"Authorization": "Bearer s"}},
        },
        "skills": {"deep-research": {"enabled": True}, "old": {"enabled": False}},
    }
    path = tmp_path / "extensions_config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    mcp, skills = inventory.parse_extensions_config(path)
    assert mcp["github"] == {"enabled": False, "transport": "stdio", "endpoint": "npx"}
    assert mcp["docs"] == {"enabled": True, "transport": "http", "endpoint": "http://x/mcp"}
    assert "secret" not in json.dumps(mcp) and "Bearer" not in json.dumps(mcp)
    assert skills == {"deep-research": True, "old": False}


def test_parse_acp_agents_from_yaml_text_without_pyyaml():
    text = 'models:\n  - name: a\nacp_agents:\n  claude_code:\n    command: npx\n    args: ["-y", "x"]\n  openclaw:\n    command: node\nskills:\n  path: skills\n'
    assert inventory.parse_acp_agents_text(text) == {"claude_code": "npx", "openclaw": "node"}
    assert inventory.parse_acp_agents_text("models: []\n") == {}
    assert inventory.parse_acp_agents_text("# acp_agents:\n#   claude_code:\n") == {}


def test_build_report_aggregates_overall_and_isolates_probe_errors():
    def ok():
        return [inventory.item("a", kind="mail", display_name="A", health="healthy", detail="")]

    def warn():
        return [inventory.item("b", kind="crm", display_name="B", health="degraded", detail="")]

    def boom():
        raise RuntimeError("probe exploded")

    report = inventory.build_report(probes=[("ok", ok), ("warn", warn), ("boom", boom)])
    assert report["gate"] == "inventory"
    assert report["overall"] == "yellow"
    assert report["ok"] is True
    names = [c["name"] for c in report["checks"]]
    assert names[:2] == ["a", "b"]
    assert report["checks"][2]["name"] == "boom"
    assert report["checks"][2]["health"] == "unknown"
    assert "probe exploded" in report["checks"][2]["detail"]
    assert isinstance(report["checked_at_epoch"], float)


def test_build_report_is_red_when_any_item_is_down():
    def down():
        return [inventory.item("db", kind="database", display_name="DB", health="down", detail="refused")]

    report = inventory.build_report(probes=[("down", down)])
    assert report["overall"] == "red"
    assert report["ok"] is False


def test_write_atomic_leaves_no_temp_file(tmp_path):
    target = tmp_path / "gates" / "inventory.json"
    inventory.write_atomic(target, {"gate": "inventory", "checks": []})
    assert json.loads(target.read_text(encoding="utf-8"))["gate"] == "inventory"
    assert [p.name for p in target.parent.iterdir()] == ["inventory.json"]


def test_main_print_only_does_not_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(inventory, "PROBES", [("none", lambda: [])])
    rc = inventory.main(["--print", "--json", "--status-path", str(tmp_path / "inv.json")])
    assert rc == 0
    assert not (tmp_path / "inv.json").exists()
    assert json.loads(capsys.readouterr().out)["gate"] == "inventory"
