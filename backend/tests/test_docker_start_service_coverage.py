"""The launcher must start every service config.yaml points a tool at.

SearXNG was defined in docker-compose-dev.yaml, wired into config.yaml as
``web_search``'s backend, and never started: ``scripts/docker.sh`` named only
"frontend gateway nginx", and compose only auto-starts what ``depends_on``
reaches (nginx -> {frontend, gateway} -> postgres). Nothing depends on searxng,
so it simply never came up. ``getent hosts searxng`` NXDOMAIN'd inside the
gateway and the Privacy panel's SearXNG card was permanently red.

It survived for weeks because ``web_search`` falls back to DuckDuckGo, so search
kept working while the badge stayed broken. This test is the missing link
between "config.yaml references a host" and "the launcher starts it".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKER_SH = REPO_ROOT / "scripts" / "docker.sh"
CONFIG = REPO_ROOT / "config.yaml"


def _started_services() -> set[str]:
    """The service names docker.sh passes to `compose up`."""
    text = DOCKER_SH.read_text(encoding="utf-8")
    names: set[str] = set()
    for match in re.finditer(r'^\s*services="([^"]*)"', text, re.MULTILINE):
        for token in match.group(1).split():
            if not token.startswith("$"):
                names.add(token)
    # `services="$services provisioner"` appends rather than redefining.
    for match in re.finditer(r'services="\$services ([^"]*)"', text):
        names.update(match.group(1).split())
    return names


def _config_service_hosts() -> set[str]:
    """Compose service names appearing as a tool `base_url` host in config.yaml."""
    if not CONFIG.exists():  # pragma: no cover - config.yaml is gitignored in CI
        pytest.skip("config.yaml not present")
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    hosts: set[str] = set()
    for tool in data.get("tools") or []:
        base_url = (tool or {}).get("base_url") or ""
        match = re.match(r"^https?://([A-Za-z0-9_-]+):\d+", str(base_url))
        if match:
            hosts.add(match.group(1))
    return hosts


def test_docker_sh_starts_every_service_a_tool_points_at():
    started = _started_services()
    required = _config_service_hosts()
    # Only names that are real compose services can be started.
    compose = yaml.safe_load((REPO_ROOT / "docker" / "docker-compose-dev.yaml").read_text(encoding="utf-8"))
    defined = set((compose.get("services") or {}).keys())

    missing = sorted((required & defined) - started)
    assert not missing, (
        f"config.yaml points tools at {missing}, and docker-compose-dev.yaml defines them, "
        f"but scripts/docker.sh never starts them. Nothing depends_on them, so they stay down "
        f"and their health cards read unhealthy forever. Started: {sorted(started)}"
    )


def test_searxng_specifically_is_started():
    """Explicit anchor for the service this test exists because of."""
    assert "searxng" in _started_services()


def test_started_services_all_exist_in_compose():
    """A typo in the list would silently start nothing."""
    compose = yaml.safe_load((REPO_ROOT / "docker" / "docker-compose-dev.yaml").read_text(encoding="utf-8"))
    defined = set((compose.get("services") or {}).keys())
    unknown = sorted(_started_services() - defined)
    assert not unknown, f"scripts/docker.sh names services that docker-compose-dev.yaml does not define: {unknown}"
