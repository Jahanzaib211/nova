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


def test_local_llm_reports_retired_not_down_when_its_unit_is_disabled(monkeypatch):
    """2026-09-22: the GPU model was switched off on purpose (it held 11.6 GB
    of VRAM and crashed every GL client); the inventory gate stayed red on
    `llama_server unreachable` for good. A disabled unit is a decision."""
    import subprocess as sp
    from types import SimpleNamespace

    import inventory

    monkeypatch.setattr(inventory.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(inventory.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="disabled\n", stderr=""))
    item = inventory._local_llm_item()
    assert item["health"] == "disabled" and "retired" in item["detail"]

    monkeypatch.setattr(inventory.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="enabled\n", stderr=""))
    monkeypatch.setattr(inventory, "http_probe", lambda url, headers=None: (None, None, "connection refused"))
    assert inventory._local_llm_item()["health"] == "down"

    def boom(*a, **k):
        raise sp.TimeoutExpired("systemctl", 5)

    monkeypatch.setattr(inventory.subprocess, "run", boom)
    assert inventory.local_llm_retired() is False


INTEGRATIONS_YAML = """\
models: []
integrations:
  enabled: true
  probe_timeout_seconds: 3
  services:
    ollama:
      kind: llm_gateway
      url: http://host.docker.internal:11434
      display_name: Ollama
      enabled: false
    litellm:
      kind: llm_gateway
      url: http://host.docker.internal:4000
      display_name: LiteLLM
      # a comment inside a service
      enabled: false   # trailing comment
    # Health probe only; UI home is the Runtimes page.
    openclaw:
      kind: agent_gateway
      url: http://host.docker.internal:18789
      display_name: OpenClaw
      capabilities: [acp]
    searxng:
      kind: search
      url: http://searxng:8080
      display_name: SearXNG
acp_agents:
  openclaw:
    command: node
"""


def test_parse_integrations_reads_urls_and_enabled_flags_without_pyyaml():
    enabled, services = inventory.parse_integrations_text(INTEGRATIONS_YAML)
    assert enabled is True
    assert list(services) == ["ollama", "litellm", "openclaw", "searxng"]
    assert services["litellm"] == {"kind": "llm_gateway", "url": "http://host.docker.internal:4000", "display_name": "LiteLLM", "enabled": False}
    assert services["openclaw"]["capabilities"] == ["acp"]
    assert "enabled" not in services["openclaw"]
    # The acp_agents block's `openclaw:` is not an integration service.
    assert services["openclaw"]["kind"] == "agent_gateway"


def test_parse_integrations_matches_pyyaml_on_the_real_config():
    yaml = pytest.importorskip("yaml")
    text = (_REPO_ROOT / "config.yaml").read_text(encoding="utf-8")
    real = yaml.safe_load(text).get("integrations") or {}
    enabled, services = inventory.parse_integrations_text(text)
    assert enabled == bool(real.get("enabled"))
    expected = real.get("services") or {}
    assert set(services) == set(expected)
    for sid, svc in expected.items():
        assert services[sid].get("url") == svc.get("url"), sid
        assert services[sid].get("enabled", True) == svc.get("enabled", True), sid


def test_host_url_translates_container_side_hosts(monkeypatch):
    monkeypatch.delenv("NOVA_INVENTORY_DOCKER_HOST", raising=False)
    assert inventory.host_url("http://host.docker.internal:4000") == f"http://{inventory.DOCKER_BRIDGE}:4000"
    monkeypatch.setenv("NOVA_INVENTORY_DOCKER_HOST", "10.0.0.1")
    assert inventory.host_url("http://host.docker.internal:18789/x") == "http://10.0.0.1:18789/x"
    assert inventory.host_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
    assert inventory.host_url("http://searxng:8080") is None
    assert inventory.compose_container("http://searxng:8080") == "deer-flow-searxng"


def test_disabled_service_is_reported_disabled_without_a_probe(monkeypatch):
    monkeypatch.setattr(inventory, "http_probe", lambda *a, **k: pytest.fail("disabled service was probed"))
    _, services = inventory.parse_integrations_text(INTEGRATIONS_YAML)
    entry = inventory.integration_item("litellm", services["litellm"])
    assert entry["health"] == "disabled" and entry["status"] == "green"
    assert "enabled: false" in entry["detail"]
    off = inventory.integration_item("openclaw", services["openclaw"], globally_enabled=False)
    assert off["health"] == "disabled"


def test_enabled_service_is_probed_at_its_config_url_and_health_path(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(inventory, "http_probe", lambda url, headers=None, timeout=3.0: seen.append(url) or (401, 2.0, ""))
    _, services = inventory.parse_integrations_text(INTEGRATIONS_YAML)
    entry = inventory.integration_item("openclaw", services["openclaw"])
    assert seen == [f"http://{inventory.DOCKER_BRIDGE}:18789/"]
    assert entry["name"] == "openclaw" and entry["health"] == "healthy" and entry["capabilities"] == ["acp"]


def test_compose_internal_service_uses_container_state():
    _, services = inventory.parse_integrations_text(INTEGRATIONS_YAML)
    ps = {"deer-flow-searxng": {"state": "running", "status": "Up 3 days (healthy)", "healthy": True}}
    entry = inventory.integration_item("searxng", services["searxng"], docker_ps=ps)
    assert entry["name"] == "nova_searxng" and entry["health"] == "healthy"
    assert inventory.integration_item("searxng", services["searxng"], docker_ps={})["health"] == "down"


def test_groups_follow_kind_and_reachability():
    _, services = inventory.parse_integrations_text(INTEGRATIONS_YAML)
    assert inventory.integration_group(services["litellm"]) == "llm"
    assert inventory.integration_group(services["openclaw"]) == "business"
    assert inventory.integration_group(services["searxng"]) == "containers"


def test_pm2_apps_follow_the_ecosystem_file(monkeypatch, tmp_path):
    eco = tmp_path / "ecosystem.config.js"
    eco.write_text('module.exports = { apps: [ { name: "a" }, /* { name: "off" }, */ // { name: "off2" }\n { name: "b" } ] };\n')
    monkeypatch.setenv("NOVA_ECOSYSTEM_FILE", str(eco))
    monkeypatch.setenv("NOVA_INVENTORY_EXTRA_PM2_APPS", "ext,a")
    assert inventory.pm2_apps_to_report() == ["a", "b", "ext"]
