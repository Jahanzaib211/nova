"""The Claude Code CLI state must reach the containers through a DIRECTORY mount.

``~/.claude.json`` used to be bind-mounted as a single file into nova-gateway
and nova-jobs. The CLI rewrites that file by write-then-rename, which gives the
host path a new inode; a single-file bind pins the inode that existed at
container creation, so the container kept reading the old copy until it was
recreated (the drift gate's ``file_mounts`` check reported it yellow).

Now ``scripts/acp-secrets.sh`` (run by the launcher on every start) stages the
file into a directory, the cli-auth overlay mounts that directory read-only,
and ``/root/.claude.json`` inside the container is a symlink into it. A
directory mount resolves names on every lookup, so a re-stage is visible at
once, with no recreation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OVERLAY = REPO_ROOT / "docker" / "docker-compose.cli-auth.yaml"
DEV_COMPOSE = REPO_ROOT / "docker" / "docker-compose-dev.yaml"
ENTRYPOINT = REPO_ROOT / "docker" / "dev-entrypoint.sh"
ACP_SECRETS = REPO_ROOT / "scripts" / "acp-secrets.sh"

STATE_TARGET = "/run/nova/claude-state"


def _overlay() -> dict:
    return yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))


def _binds(service: str) -> list[dict]:
    return [v for v in _overlay()["services"][service]["volumes"] if isinstance(v, dict) and v.get("type") == "bind"]


def _env(service: str) -> dict[str, str]:
    raw = _overlay()["services"][service].get("environment") or []
    if isinstance(raw, dict):
        return {k: str(v) for k, v in raw.items()}
    return dict(item.split("=", 1) for item in raw)


def _command_text(cmd) -> str:
    return " ".join(cmd) if isinstance(cmd, list) else str(cmd)


def test_no_single_file_mount_of_claude_json():
    for svc in ("gateway", "jobs"):
        for m in _binds(svc):
            assert not m["source"].endswith(".claude.json"), f"{svc}: {m['source']} is a single-file bind and goes stale on the CLI's rename"
            assert not m["target"].endswith(".claude.json"), f"{svc}: {m['target']} is a single-file bind target"


def test_state_dir_is_mounted_read_only_in_both_services():
    for svc in ("gateway", "jobs"):
        mounts = {m["target"]: m for m in _binds(svc)}
        assert STATE_TARGET in mounts, f"{svc} does not mount the Claude state directory"
        assert mounts[STATE_TARGET]["read_only"] is True
        assert "claude-state" in mounts[STATE_TARGET]["source"]
        # The login dir stays read-only on purpose (backend/CLAUDE.md: the
        # runtime's own Bash is unavailable by design; the sandbox is the boundary).
        assert mounts["/root/.claude"]["read_only"] is True
        assert _env(svc).get("NOVA_CLAUDE_STATE_FILE") == f"{STATE_TARGET}/.claude.json"


def test_jobs_command_links_the_state_file_and_keeps_the_worker_command():
    overlay_cmd = _command_text(_overlay()["services"]["jobs"]["command"])
    base_cmd = yaml.safe_load(DEV_COMPOSE.read_text(encoding="utf-8"))["services"]["jobs"]["command"]
    # Base: ["sh", "-c", "<script>"]; the overlay must run the same script after the link.
    assert base_cmd[:2] == ["sh", "-c"]
    assert "ln -sfn" in overlay_cmd and "/root/.claude.json" in overlay_cmd
    assert overlay_cmd.rstrip().endswith(base_cmd[2]), "the overlay's jobs command drifted from docker-compose-dev.yaml's"


def test_dev_entrypoint_links_the_state_file():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'ln -sfn "$NOVA_CLAUDE_STATE_FILE" /root/.claude.json' in text


def _stage(home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "OPENCLAW_CONFIG": str(home / "missing.json")}
    env.update(extra_env or {})
    return subprocess.run(["sh", str(ACP_SECRETS)], env=env, capture_output=True, text=True, check=False)


def test_acp_secrets_stages_claude_json_into_a_private_directory(tmp_path):
    (tmp_path / ".claude.json").write_text('{"v": 1}', encoding="utf-8")
    proc = _stage(tmp_path)
    assert proc.returncode == 0, proc.stderr
    staged = tmp_path / ".nova" / "claude-state" / ".claude.json"
    assert staged.read_text(encoding="utf-8") == '{"v": 1}'
    assert (staged.stat().st_mode & 0o777) == 0o600
    assert ((tmp_path / ".nova" / "claude-state").stat().st_mode & 0o777) == 0o700


def test_restaging_after_a_rename_rewrite_is_seen_through_the_directory(tmp_path):
    src = tmp_path / ".claude.json"
    src.write_text('{"v": 1}', encoding="utf-8")
    assert _stage(tmp_path).returncode == 0
    # The CLI's own write pattern: new file, rename over the old path.
    tmp = tmp_path / ".claude.json.tmp"
    tmp.write_text('{"v": 2}', encoding="utf-8")
    tmp.replace(src)
    assert _stage(tmp_path).returncode == 0
    assert (tmp_path / ".nova" / "claude-state" / ".claude.json").read_text(encoding="utf-8") == '{"v": 2}'


def test_missing_claude_json_is_not_fatal(tmp_path):
    proc = _stage(tmp_path)
    assert proc.returncode == 0
    assert "claude" in proc.stderr.lower()


def test_acp_secrets_parses():
    proc = subprocess.run(["sh", "-n", str(ACP_SECRETS)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
