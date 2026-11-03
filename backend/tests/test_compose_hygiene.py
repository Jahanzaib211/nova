"""Docker hygiene for every long-running service Nova ships compose files for.

* Log rotation on every service: the daemon on the deployment host has no
  default log-opts, so json-file logs were unbounded and could fill the disk.
* restart policy, memory limit and healthcheck on every long-running service
  (this host has an OOM history; an unbounded container takes others down).
* No floating image tags (`:latest`, `:alpine`, `:pg16`, `:1`): two pulls a
  week apart must not differ with no repo change.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKER = REPO_ROOT / "docker"

# Files that define services (overlays that only extend existing services
# inherit everything from these).
SERVICE_FILES = [
    DOCKER / "docker-compose-dev.yaml",
    DOCKER / "docker-compose.nova-prod.yaml",
    DOCKER / "docker-compose.yaml",
    DOCKER / "docker-compose.local-llm.yaml",
    DOCKER / "docker-compose.tracing.yaml",
    DOCKER / "monitoring" / "docker-compose.yaml",
]

# Monitoring exporters whose images were not checked for a probe binary; the
# stack is not part of the PM2 deployment (nova-monitoring was retired).
NO_HEALTHCHECK_OK = {("monitoring/docker-compose.yaml", name) for name in ("alloy", "node_exporter", "cadvisor")}

# Images we build ourselves (tags, not pulls).
LOCAL_IMAGE = re.compile(r"^(\$\{[A-Z_]+:-)?nova-[a-z-]+(-prod)?:")
PINNED = re.compile(r"(@sha256:[0-9a-f]{64}$)|(:v?\d+\.\d+[\w.\-]*$)")


class _Loader(yaml.SafeLoader):
    """SafeLoader that tolerates compose's merge tags (``!reset``, ``!override``)."""


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _load(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)  # noqa: S506 - SafeLoader subclass


def _services():
    for path in SERVICE_FILES:
        data = _load(path)
        for name, svc in (data.get("services") or {}).items():
            yield path, name, svc


def _ids():
    return [f"{p.parent.name}/{p.name}:{n}" for p, n, _ in _services()]


@pytest.mark.parametrize(("path", "name", "svc"), list(_services()), ids=_ids())
def test_every_service_rotates_its_logs(path, name, svc):
    logging = svc.get("logging") or {}
    assert logging.get("driver") == "json-file", f"{path.name}:{name} has no json-file logging"
    opts = logging.get("options") or {}
    assert opts.get("max-size") and opts.get("max-file"), f"{path.name}:{name} logs are unbounded"


@pytest.mark.parametrize(("path", "name", "svc"), list(_services()), ids=_ids())
def test_every_service_has_restart_memory_and_health(path, name, svc):
    assert svc.get("restart") in ("unless-stopped", "always"), f"{path.name}:{name} has no restart policy"
    mem = svc.get("mem_limit") or (((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}).get("memory")
    assert mem, f"{path.name}:{name} has no memory limit"
    if (f"{path.parent.name}/{path.name}", name) not in NO_HEALTHCHECK_OK:
        assert "healthcheck" in svc, f"{path.name}:{name} has no healthcheck"


def _all_images():
    for path in sorted(DOCKER.rglob("*.y*ml")):
        data = _load(path) or {}
        for name, svc in (data.get("services") or {}).items() if isinstance(data, dict) else []:
            if isinstance(svc, dict) and svc.get("image"):
                yield f"{path.relative_to(REPO_ROOT)}:{name}", str(svc["image"])
    for values in (REPO_ROOT / "k8s" / "charts" / "nova").glob("values*.yaml"):
        images = (_load(values) or {}).get("images") or {}
        for key, image in images.items():
            if key == "sandbox":  # built locally (make sandbox-image), not pulled
                continue
            yield f"{values.relative_to(REPO_ROOT)}:{key}", str(image)


@pytest.mark.parametrize(("where", "image"), list(_all_images()))
def test_no_floating_image_tags(where, image):
    if LOCAL_IMAGE.match(image):
        return
    assert PINNED.search(image), f"{where}: {image!r} floats — pin a digest or a full version"


def test_dockerfiles_pin_the_docker_cli_stage():
    text = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "--from=docker:" in line:
            assert "@sha256:" in line, line
