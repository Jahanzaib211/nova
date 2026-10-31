#!/usr/bin/env python3
"""Machine inventory producer: what can Nova reach on this box right now?

Writes ``~/.nova/gates/inventory.json`` in the gate shape the ops console
already renders (``gate``, ``checked_at_epoch``, ``overall``, ``ok``,
``checks[]``). Each check additionally carries the fields from
``contracts/integrations_health_contract.json`` (``kind``, ``health``,
``endpoint``, ``latency_ms``, ``capabilities``) so the in-app Settings >
Integrations page and the console describe the same thing with the same
words.

Service URLs and enabled flags come from ``config.yaml``
``integrations.services`` — the same block that page is built from — rather
than a second, hand-maintained list here. Container-side hostnames are
translated for the host (see ``host_url``).

Scheduled by ``scripts/gates/gates-daemon.py`` (see ``build_producers``).
Stdlib only; probes are read-only and bounded by short timeouts. It never
prints secrets: MCP env/headers are dropped at parse time.

    python3 scripts/inventory.py            # write + summary
    python3 scripts/inventory.py --json     # also echo the document
    python3 scripts/inventory.py --print    # never write
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "contracts" / "integrations_health_contract.json"

GREEN, YELLOW, RED = "green", "yellow", "red"

# Contract vocabulary (pinned by backend/tests/test_inventory_script.py).
HEALTH_TO_GATE = {
    "healthy": GREEN,
    "disabled": GREEN,
    "degraded": YELLOW,
    "unknown": YELLOW,
    "down": RED,
}
KINDS = (
    "mcp_server",
    "skill",
    "acp_agent",
    "llm_gateway",
    "mail",
    "crm",
    "helpdesk",
    "search",
    "crawler",
    "browser",
    "database",
    "agent_gateway",
)

TIMEOUT = 3.0
DOCKER_BRIDGE = "172.17.0.1"


# --------------------------------------------------------------------------- shaping


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


def item(
    name: str,
    *,
    kind: str,
    display_name: str,
    health: str,
    detail: str,
    endpoint: str | None = None,
    latency_ms: float | None = None,
    capabilities: list[str] | None = None,
    **extra,
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown integration kind: {kind}")
    if health not in HEALTH_TO_GATE:
        raise ValueError(f"unknown health status: {health}")
    return {
        # gate/console fields
        "name": name,
        "status": HEALTH_TO_GATE[health],
        "detail": detail,
        # contract item fields
        "id": name,
        "kind": kind,
        "display_name": display_name,
        "endpoint": endpoint,
        "health": health,
        "latency_ms": None if latency_ms is None else round(latency_ms, 1),
        "checked_at": _now_iso(),
        "capabilities": list(capabilities or []),
        **extra,
    }


# --------------------------------------------------------------------------- primitives


def tcp_probe(host: str, port: int, timeout: float = TIMEOUT) -> tuple[bool, float, str]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, (time.perf_counter() - started) * 1000, ""
    except OSError as exc:
        return False, (time.perf_counter() - started) * 1000, str(exc)


def http_probe(url: str, headers: dict[str, str] | None = None, timeout: float = TIMEOUT) -> tuple[int | None, float, str]:
    """Return (status, latency_ms, body_prefix). ``status`` is None on a connection error."""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "nova-inventory"})
    # Local probes must never go through a proxy from the environment.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310 - fixed local URLs
            return resp.status, (time.perf_counter() - started) * 1000, resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, (time.perf_counter() - started) * 1000, ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, (time.perf_counter() - started) * 1000, str(exc)


def banner_probe(host: str, port: int, use_tls: bool = False, timeout: float = TIMEOUT) -> tuple[str | None, float]:
    started = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        if use_tls:
            # Liveness only: the cert is for the public mail hostname, not 127.0.0.1.
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=host)
        with sock:
            line = sock.recv(256).decode("utf-8", "replace").strip()
        return line, (time.perf_counter() - started) * 1000
    except (OSError, ssl.SSLError):
        return None, (time.perf_counter() - started) * 1000


def run(argv: list[str], timeout: float = 15.0) -> str | None:
    if shutil.which(argv[0]) is None:
        return None
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


# --------------------------------------------------------------------------- parsers (pure)


def parse_docker_ps(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        status = str(row.get("Status", ""))
        healthy: bool | None = None
        if "(healthy)" in status:
            healthy = True
        elif "(unhealthy)" in status or str(row.get("State")) != "running":
            healthy = False
        out[str(row.get("Names", ""))] = {"state": row.get("State"), "status": status, "image": row.get("Image"), "healthy": healthy}
    return out


def container_health(state: dict | None) -> str:
    if not state or state.get("state") != "running":
        return "down"
    return "degraded" if state.get("healthy") is False else "healthy"


def parse_pm2_jlist(text: str) -> dict[str, dict]:
    try:
        rows = json.loads(text or "")
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict] = {}
    for row in rows if isinstance(rows, list) else []:
        env = row.get("pm2_env", {}) if isinstance(row, dict) else {}
        out[str(row.get("name"))] = {"status": env.get("status"), "restarts": env.get("restart_time", 0)}
    return out


def parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        digits = "".join(ch for ch in rest if ch.isdigit())
        if digits:
            values[key.strip()] = int(digits)
    swap_total, swap_free = values.get("SwapTotal", 0), values.get("SwapFree", 0)
    return {
        "mem_total_kb": values.get("MemTotal", 0),
        "mem_available_kb": values.get("MemAvailable", 0),
        "swap_used_kb": max(swap_total - swap_free, 0),
    }


def parse_extensions_config(path: Path) -> tuple[dict[str, dict], dict[str, bool]]:
    """MCP servers (secrets dropped) and skill enabled-state from extensions_config.json."""
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    mcp: dict[str, dict] = {}
    for name, server in (cfg.get("mcpServers") or {}).items():
        if not isinstance(server, dict):
            continue
        transport = server.get("type") or server.get("transport") or "stdio"
        mcp[name] = {
            "enabled": bool(server.get("enabled", True)),
            "transport": transport,
            "endpoint": server.get("url") if transport != "stdio" else server.get("command"),
        }
    skills = {name: bool(state.get("enabled", True)) for name, state in (cfg.get("skills") or {}).items() if isinstance(state, dict)}
    return mcp, skills


def parse_acp_agents_text(text: str) -> dict[str, str]:
    """Top-level ``acp_agents:`` block of config.yaml → {agent: command}, without PyYAML.

    Only the two-space-indented agent names and their ``command:`` lines are
    read; anything commented out is ignored.
    """
    agents: dict[str, str] = {}
    in_block = False
    current: str | None = None
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        if not raw.startswith(" ") and raw.strip():
            in_block = raw.strip() == "acp_agents:"
            current = None
            continue
        if not in_block:
            continue
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 2 and stripped.endswith(":"):
            current = stripped[:-1]
            agents[current] = ""
        elif indent == 4 and current and stripped.startswith("command:"):
            agents[current] = stripped.split(":", 1)[1].strip().strip("\"'")
    return agents


# --------------------------------------------------------------------------- probes


def _http_item(name: str, kind: str, display: str, url: str, *, ok_codes=(200,), capabilities=None, headers=None) -> dict:
    status, latency, body = http_probe(url, headers=headers)
    if status is None:
        return item(name, kind=kind, display_name=display, health="down", detail=f"unreachable: {body[:120]}", endpoint=url, latency_ms=latency)
    if status in ok_codes:
        return item(name, kind=kind, display_name=display, health="healthy", detail=f"HTTP {status}", endpoint=url, latency_ms=latency, capabilities=capabilities)
    return item(name, kind=kind, display_name=display, health="degraded", detail=f"HTTP {status}", endpoint=url, latency_ms=latency)


def _yaml_scalar(raw: str):
    """One YAML scalar or flow list, the subset config.yaml's integrations use."""
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        return [_yaml_scalar(part) for part in value[1:-1].split(",") if part.strip()]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    low = value.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    return value


def parse_integrations_text(text: str) -> tuple[bool, dict[str, dict]]:
    """``integrations:`` of config.yaml → (enabled, {service_id: fields}), without PyYAML.

    The same block Settings › Integrations is built from, so this inventory
    and that page describe the same services. Reads the shape config.yaml
    uses: ``integrations.enabled`` at two spaces, ``services:`` at two, each
    service id at four and its scalar/flow-list fields at six. Comments
    (whole-line and trailing `` #``) are ignored.
    """
    enabled = False
    services: dict[str, dict] = {}
    in_block = in_services = False
    current: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = raw.split(" #", 1)[0].rstrip()
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            in_block = stripped == "integrations:"
            in_services = False
            current = None
            continue
        if not in_block:
            continue
        key, _, value = stripped.partition(":")
        if indent == 2:
            in_services = key == "services" and not value.strip()
            current = None
            if key == "enabled":
                enabled = _yaml_scalar(value) is True
        elif in_services and indent == 4 and not value.strip():
            current = key
            services[current] = {}
        elif in_services and indent == 6 and current:
            services[current][key] = _yaml_scalar(value)
    return enabled, services


def load_integrations(path: Path | None = None) -> tuple[bool, dict[str, dict]]:
    try:
        return parse_integrations_text((path or REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    except OSError:
        return False, {}


# Probe path and accepted codes per service id — mirrors
# backend/packages/harness/deerflow/integrations/clients.py so the inventory
# asks each service the same question its Settings card does. A config
# `health_path` overrides, as it does there.
SERVICE_PROBES: dict[str, tuple[str, tuple[int, ...]]] = {
    "ollama": ("/api/tags", (200,)),
    "litellm": ("/health/liveliness", (200,)),
    "mailcow": ("/api/v1/get/status/version", (200, 401)),
    "chatwoot": ("/api", (200,)),
    "twenty": ("/healthz", (200,)),
    "openclaw": ("/", (200, 401, 403)),
    "searxng": ("/healthz", (200,)),
    "crawl4ai": ("/health", (200,)),
    "browserless": ("/pressure", (200, 401, 403)),
}
# Inventory item ids that predate config-driven probing; kept so the console's
# history of each check does not fork.
ITEM_IDS = {"mailcow": "mailcow_api"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def host_url(url: str) -> str | None:
    """Translate a container-side integration URL into one reachable from this host.

    ``host.docker.internal`` is the docker bridge from the host's side
    (NOVA_INVENTORY_DOCKER_HOST, default 172.17.0.1) — the same address the
    gateway container's request lands on, so a service only reachable through
    the host bridge is judged the way Nova sees it. Loopback and IP literals
    are used as-is. Any other hostname is a compose service name that only
    resolves on the docker network: None, and the caller checks its container.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if host == "host.docker.internal":
        bridge = os.environ.get("NOVA_INVENTORY_DOCKER_HOST", DOCKER_BRIDGE)
        netloc = bridge + (f":{parts.port}" if parts.port else "")
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    if host in _LOCAL_HOSTS or host.replace(".", "").isdigit():
        return url
    return None


def compose_container(url: str) -> str:
    """Container name for a compose-internal service URL (``http://searxng:8080`` → ``deer-flow-searxng``)."""
    prefix = os.environ.get("NOVA_COMPOSE_CONTAINER_PREFIX", "deer-flow-")
    return prefix + (urllib.parse.urlsplit(url).hostname or "")


def integration_group(svc: dict) -> str:
    """Which inventory section a configured service is reported under."""
    if host_url(str(svc.get("url", ""))) is None:
        return "containers"
    kind = svc.get("kind")
    return {"llm_gateway": "llm", "mail": "mail"}.get(kind, "business")


def integration_item(sid: str, svc: dict, *, docker_ps: dict[str, dict] | None = None, globally_enabled: bool = True) -> dict:
    """One configured integration → one inventory item."""
    url = str(svc.get("url", "")).rstrip("/")
    kind = svc.get("kind") if svc.get("kind") in KINDS else "agent_gateway"
    display = str(svc.get("display_name") or sid)
    caps = [str(c) for c in svc.get("capabilities") or []]
    reachable = host_url(url)
    if reachable is None:
        name = f"nova_{sid}"
        endpoint = compose_container(url)
    else:
        name = ITEM_IDS.get(sid, sid)
        port = urllib.parse.urlsplit(url).port
        display = f"{display} (:{port})" if port else display
        path, _ = SERVICE_PROBES.get(sid, ("/", (200,)))
        endpoint = reachable.rstrip("/") + str(svc.get("health_path") or path)
    if not globally_enabled or svc.get("enabled") is False:
        why = "integrations.enabled is false" if not globally_enabled else "enabled: false"
        return item(name, kind=kind, display_name=display, health="disabled", detail=f"disabled in config.yaml ({why})", endpoint=endpoint, capabilities=[])

    if reachable is None:
        state = (docker_ps or {}).get(endpoint)
        return item(name, kind=kind, display_name=display, health=container_health(state), detail=(state or {}).get("status", "container not found"), endpoint=endpoint, capabilities=[])

    _, ok_codes = SERVICE_PROBES.get(sid, ("/", (200,)))
    key_env = svc.get("api_key_env")
    key = os.environ.get(str(key_env), "").strip() if key_env else ""
    headers = None
    if sid == "mailcow" and key:
        headers = {"X-API-Key": key}
    if sid == "ollama":
        status, latency, body = http_probe(endpoint)
        if status == 200:
            try:
                models = [m.get("name", "?") for m in json.loads(body).get("models", [])]
            except (json.JSONDecodeError, AttributeError):
                models = []
            return item(name, kind=kind, display_name=display, health="healthy", detail=f"{len(models)} models: {', '.join(models)[:160]}", endpoint=endpoint, latency_ms=latency, capabilities=models)
        return item(name, kind=kind, display_name=display, health="down" if status is None else "degraded", detail=f"HTTP {status}" if status else f"unreachable: {body[:120]}", endpoint=endpoint, latency_ms=latency)
    entry = _http_item(name, kind, display, endpoint, ok_codes=ok_codes, headers=headers, capabilities=caps)
    if sid == "mailcow" and entry["health"] == "healthy" and not key:
        entry["detail"] += f" ({key_env or 'MAILCOW_API_KEY'} not set)"
        entry["capabilities"] = []
    return entry


def probe_integrations(group: str) -> list[dict]:
    """Every config.yaml integration that belongs to ``group``, probed."""
    globally_enabled, services = load_integrations()
    wanted = {sid: svc for sid, svc in services.items() if integration_group(svc) == group}
    docker_ps = parse_docker_ps(run(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=20) or "") if group == "containers" and wanted else {}
    return [integration_item(sid, svc, docker_ps=docker_ps, globally_enabled=globally_enabled) for sid, svc in wanted.items()]


LOCAL_LLM_UNIT = "local-llm.service"


def local_llm_retired(unit: str = LOCAL_LLM_UNIT) -> bool:
    """True when the operator has switched the local model off on purpose.

    The GPU-resident llama-server is a user systemd unit. Stopping it to give
    the GPU back (2026-09-22: it held 11.6 of 12 GB and every GL client on the
    box crashed) is a decision, not an outage — but the probe painted it red
    and kept the inventory gate red for good. Disabled/masked/absent unit ⇒
    retired ⇒ reported as `disabled` (green), same as the retired Ollama.
    An *enabled* unit that does not answer is still `down`.
    """
    if not shutil.which("systemctl"):
        return False
    try:
        out = subprocess.run(["systemctl", "--user", "is-enabled", unit], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    state = (out.stdout or out.stderr or "").strip().splitlines()
    return not state or state[0] in {"disabled", "masked", "not-found"}


def _local_llm_item() -> dict:
    url = "http://127.0.0.1:8086/v1/models"
    if local_llm_retired():
        return item("llama_server", kind="llm_gateway", display_name="llama-server (:8086)", health="disabled", detail=f"retired ({LOCAL_LLM_UNIT} disabled)", endpoint=url, latency_ms=None)
    return _http_item("llama_server", "llm_gateway", "llama-server (:8086)", url)


def probe_nova_stack() -> list[dict]:
    items = [
        _http_item("nova_nginx", "agent_gateway", "Nova (nginx :2026)", "http://127.0.0.1:2026/health"),
        _http_item("nova_gateway", "agent_gateway", "Nova gateway (:8001)", "http://127.0.0.1:8001/health"),
    ]
    ok, latency, err = tcp_probe("127.0.0.1", int(os.environ.get("NOVA_PG_PORT", "5433")))
    items.append(item("nova_postgres", kind="database", display_name="Nova Postgres", health="healthy" if ok else "down", detail="tcp ok" if ok else err, endpoint="127.0.0.1:5433", latency_ms=latency))
    return items


def probe_llm() -> list[dict]:
    # Ollama, LiteLLM, ...: whatever config.yaml declares as an llm_gateway,
    # with its enabled flag honoured (a disabled service is `disabled`, green).
    return [*probe_integrations("llm"), _local_llm_item()]


def probe_mail() -> list[dict]:
    items = []
    banner, latency = banner_probe("127.0.0.1", 25)
    items.append(
        item(
            "mailcow_smtp",
            kind="mail",
            display_name="Mailcow SMTP (:25)",
            health="healthy" if banner and banner.startswith("220") else "down",
            detail=(banner or "no banner")[:120],
            endpoint="host.docker.internal:25",
            latency_ms=latency,
            capabilities=["smtp"],
        )
    )
    banner, latency = banner_probe("127.0.0.1", 993, use_tls=True)
    items.append(
        item(
            "mailcow_imap",
            kind="mail",
            display_name="Mailcow IMAPS (:993)",
            health="healthy" if banner and banner.startswith("* OK") else "down",
            detail=(banner or "no banner")[:120],
            endpoint="host.docker.internal:993",
            latency_ms=latency,
            capabilities=["imap"],
        )
    )
    items.extend(probe_integrations("mail"))
    return items


def probe_business() -> list[dict]:
    # Chatwoot, Twenty, OpenClaw, ... from config.yaml integrations.services.
    return probe_integrations("business")


def probe_containers() -> list[dict]:
    # Integrations addressed by compose service name (searxng, crawl4ai,
    # browserless): only resolvable on the docker network, so their container
    # state is the host-side signal.
    return probe_integrations("containers")


def probe_extensions() -> list[dict]:
    mcp, skills = parse_extensions_config(REPO_ROOT / "extensions_config.json")
    items = []
    for name, server in mcp.items():
        items.append(
            item(
                f"mcp_{name}",
                kind="mcp_server",
                display_name=f"MCP: {name}",
                health="unknown" if server["enabled"] else "disabled",
                detail=f"{server['transport']} · {'enabled' if server['enabled'] else 'disabled'} (not probed on host)",
                endpoint=server["endpoint"],
                capabilities=[],
            )
        )
    public = sorted(p.parent.name for p in (REPO_ROOT / "skills" / "public").glob("*/SKILL.md"))
    custom = sorted(p.parent.name for p in (REPO_ROOT / "skills" / "custom").glob("*/SKILL.md"))
    enabled = [s for s in public + custom if skills.get(s, True)]
    items.append(
        item("skills", kind="skill", display_name="Skills", health="healthy" if public else "degraded", detail=f"{len(public)} public + {len(custom)} custom, {len(enabled)} enabled", endpoint=str(REPO_ROOT / "skills"), capabilities=enabled)
    )
    try:
        agents = parse_acp_agents_text((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    except OSError:
        agents = {}
    if not agents:
        items.append(item("acp_agents", kind="acp_agent", display_name="ACP agents", health="disabled", detail="no acp_agents in config.yaml", capabilities=[]))
    for name, command in agents.items():
        found = shutil.which(command) is not None if command else False
        items.append(
            item(f"acp_{name}", kind="acp_agent", display_name=f"ACP: {name}", health="healthy" if found else "degraded", detail=f"command {command!r} {'found' if found else 'not found'} on host", endpoint=command, capabilities=["acp"])
        )
    return items


def probe_cli_tools() -> list[dict]:
    items = []
    for name, display, argv in (("claude_cli", "Claude Code CLI", ["claude", "--version"]), ("openclaw_cli", "OpenClaw CLI", ["openclaw", "--version"])):
        out = run(argv, timeout=20)
        items.append(item(name, kind="acp_agent", display_name=display, health="healthy" if out else "down", detail=(out or "not installed").strip().splitlines()[0][:80], endpoint=shutil.which(argv[0]), capabilities=[]))
    return items


def pm2_apps_to_report() -> list[str]:
    """The apps ecosystem.config.js declares (comments stripped, so an app
    commented out on purpose is not reported missing), plus apps supervised
    from other repos (NOVA_INVENTORY_EXTRA_PM2_APPS, default nova-ops)."""
    extra = [a.strip() for a in os.environ.get("NOVA_INVENTORY_EXTRA_PM2_APPS", "nova-ops").split(",") if a.strip()]
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import stack_config  # type: ignore[import-not-found]

        declared = stack_config.declared_pm2_apps(stack_config.read_ecosystem() or "")
    except Exception:  # noqa: BLE001 - fall back to the stack's core apps
        declared = []
    declared = declared or ["nova", "nova-healthcheck", "nova-gates", "nova-host-bridge"]
    return declared + [a for a in extra if a not in declared]


def probe_pm2() -> list[dict]:
    apps = parse_pm2_jlist(run(["pm2", "jlist"], timeout=20) or "")
    items = []
    for name in pm2_apps_to_report():
        app = apps.get(name)
        health = "healthy" if app and app["status"] == "online" else ("down" if app else "unknown")
        items.append(item(f"pm2_{name}", kind="agent_gateway", display_name=f"pm2: {name}", health=health, detail=f"{app['status']} · {app['restarts']} restarts" if app else "not registered", capabilities=[]))
    return items


def probe_resources() -> list[dict]:
    items = []
    disk = shutil.disk_usage("/")
    pct = disk.used / disk.total * 100
    items.append(item("disk_root", kind="database", display_name="Disk /", health="healthy" if pct < 85 else ("degraded" if pct < 95 else "down"), detail=f"{pct:.0f}% used, {disk.free / 1e9:.0f} GB free", capabilities=[]))
    try:
        mem = parse_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
        avail_pct = mem["mem_available_kb"] / max(mem["mem_total_kb"], 1) * 100
        items.append(item("memory", kind="database", display_name="Memory", health="healthy" if avail_pct > 20 else "degraded", detail=f"{avail_pct:.0f}% available, swap used {mem['swap_used_kb'] / 1e6:.1f} GB", capabilities=[]))
    except OSError:
        pass
    gpu = run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"], timeout=10)
    if gpu:
        items.append(item("gpu", kind="llm_gateway", display_name="GPU", health="healthy", detail=gpu.strip()[:120], capabilities=["cuda"]))
    return items


PROBES: list[tuple[str, Callable[[], list[dict]]]] = [
    ("nova_stack", probe_nova_stack),
    ("llm", probe_llm),
    ("mail", probe_mail),
    ("business", probe_business),
    ("containers", probe_containers),
    ("extensions", probe_extensions),
    ("cli_tools", probe_cli_tools),
    ("pm2", probe_pm2),
    ("resources", probe_resources),
]


# --------------------------------------------------------------------------- report


def build_report(probes: list[tuple[str, Callable[[], list[dict]]]] | None = None) -> dict:
    checks: list[dict] = []
    for name, fn in probes if probes is not None else PROBES:
        try:
            checks.extend(fn())
        except Exception as exc:  # noqa: BLE001 - one broken probe must not hide the rest
            checks.append(item(name, kind="agent_gateway", display_name=name, health="unknown", detail=f"probe raised: {exc}"))
    overall = RED if any(c["status"] == RED for c in checks) else (YELLOW if any(c["status"] == YELLOW for c in checks) else GREEN)
    return {
        "gate": "inventory",
        "checked_at_epoch": time.time(),
        "overall": overall,
        "ok": overall != RED,
        "checks": checks,
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".inventory-", suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = Path(handle.name)
    tmp.replace(path)


def _default_status_path() -> Path:
    override = os.environ.get("NOVA_INVENTORY_STATUS_PATH", "").strip()
    return Path(override) if override else Path.home() / ".nova" / "gates" / "inventory.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="echo the JSON document")
    parser.add_argument("--print", dest="print_only", action="store_true", help="print only; do not write the status file")
    parser.add_argument("--status-path", type=Path, default=_default_status_path())
    args = parser.parse_args(argv)

    report = build_report()
    if not args.print_only:
        try:
            write_atomic(args.status_path, report)
        except OSError as exc:
            print(f"inventory: could not write {args.status_path}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        icon = {GREEN: "OK  ", YELLOW: "WARN", RED: "FAIL"}
        print(f"inventory: {report['overall'].upper()} ({len(report['checks'])} items)")
        for entry in report["checks"]:
            print(f"  [{icon[entry['status']]}] {entry['name']:<22} {entry['health']:<9} {entry['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
