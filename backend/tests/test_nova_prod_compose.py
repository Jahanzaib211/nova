"""Production stack (docker/docker-compose.nova-prod.yaml) readiness contract.

The public deployment still runs the dev stack, whose gateway is
``uvicorn --reload`` on the working tree: saving any backend file restarts
production (it killed a live user run on 2026-11-01). The prod file is the way
out, and these tests pin the things that made it unsafe to switch to:

* building it re-tagged the dev stack's ``nova-gateway`` image;
* it had no jobs worker, no schema step, no postgres extra by default;
* searxng was unpinned on a network of pinned addresses;
* the launcher gave it none of the voice / local-LLM / ACP overlays.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKER = REPO_ROOT / "docker"
PROD = DOCKER / "docker-compose.nova-prod.yaml"
DEV = DOCKER / "docker-compose-dev.yaml"
PROD_ACP = DOCKER / "docker-compose.nova-prod.acp.yaml"
LAUNCHER = REPO_ROOT / "scripts" / "pm2-nova.sh"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _cmd(svc: dict) -> str:
    cmd = svc["command"]
    return " ".join(cmd) if isinstance(cmd, list) else str(cmd)


def test_built_images_never_reuse_the_dev_stack_tags():
    services = _load(PROD)["services"]
    for name in ("gateway", "frontend"):
        image = services[name].get("image", "")
        assert image.startswith(f"nova-{name}-prod:"), f"{name} must build to its own tag, got {image!r}"
    # jobs runs the gateway's image and never builds.
    assert services["jobs"]["image"] == services["gateway"]["image"]
    assert "build" not in services["jobs"]


def test_gateway_has_no_reload_and_runs_the_schema_step():
    gw = _load(PROD)["services"]["gateway"]
    cmd = _cmd(gw)
    assert "--reload" not in cmd
    assert "--no-sync" in cmd
    assert "/usr/local/bin/db-migrate.sh" in cmd
    assert "NOVA_DB_MIGRATE" in cmd
    assert gw["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert "127.0.0.1:8001:8001" in gw["ports"]


def test_gateway_image_defaults_include_the_postgres_extra():
    extras = _load(PROD)["services"]["gateway"]["build"]["args"]["UV_EXTRAS"]
    default = re.search(r":-([A-Za-z0-9_,-]+)\}+$", extras)
    assert default and "postgres" in default.group(1).split(","), extras


def test_jobs_worker_exists_and_matches_the_dev_command():
    prod = _load(PROD)["services"]["jobs"]
    dev = _load(DEV)["services"]["jobs"]
    assert prod["command"] == dev["command"]
    assert prod["depends_on"]["gateway"]["condition"] == "service_healthy"
    assert prod["user"] == "1000:1000"
    env = dict(e.split("=", 1) for e in prod["environment"])
    assert env["NOVA_CONFIG_PATH"] == "/app/backend/config.yaml"
    assert env["HOME"] == "/tmp"


def test_every_service_on_the_network_is_pinned_like_the_dev_stack():
    prod = _load(PROD)["services"]
    dev = _load(DEV)["services"]
    seen: dict[str, str] = {}
    for name, svc in prod.items():
        nets = svc.get("networks")
        if not nets:
            continue
        assert isinstance(nets, dict), f"{name}: unpinned (list form) on a network of pinned addresses"
        ip = nets["nova"]["ipv4_address"]
        assert ip not in seen, f"{name} and {seen[ip]} both pinned to {ip}"
        seen[ip] = name
        if name in dev and isinstance(dev[name].get("networks"), dict):
            assert dev[name]["networks"]["nova"]["ipv4_address"] == ip, f"{name} moved address between stacks"


def test_network_and_volume_names_match_the_dev_stack():
    prod, dev = _load(PROD), _load(DEV)
    assert prod["name"] == dev["name"]
    assert prod["networks"]["nova"]["name"] == dev["networks"]["nova"]["name"] == "nova"
    assert prod["networks"]["nova"]["ipam"] == dev["networks"]["nova"]["ipam"]
    assert "nova-postgres-data" in prod["volumes"]


def test_prod_acp_overlay_makes_root_usable_for_uid_1000():
    overlay = _load(PROD_ACP)["services"]
    for name in ("gateway", "jobs"):
        svc = overlay[name]
        assert any(t.startswith("/root:") and "uid=1000" in t for t in svc["tmpfs"])
        assert "HOME=/root" in svc["environment"]
        targets = [v["target"] for v in svc["volumes"]]
        # The dev overlay's root-owned npm cache volume must not come along.
        assert "/root/.npm" not in targets


def test_launcher_gives_the_prod_stack_the_opt_in_overlays():
    text = LAUNCHER.read_text(encoding="utf-8")
    case_end = text.index("esac\n", text.index('case "$NOVA_STACK" in'))
    after = text[case_end:]
    # Voice, local LLM and ACP are decided after the stack case, i.e. for both.
    for needle in ("docker-compose.voice.yaml", "docker-compose.local-llm.yaml", "docker-compose.cli-auth.yaml", "docker-compose.nova-prod.acp.yaml"):
        assert needle in after, needle
    assert "--scale jobs=0" in after


def test_dockerfile_bakes_the_schema_step_and_context_excludes_runtime_data():
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "scripts/db-migrate.sh /usr/local/bin/db-migrate.sh" in dockerfile
    ignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "!scripts/db-migrate.sh" in ignore
    assert "backend/.nova" in ignore
