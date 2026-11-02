"""scripts/stack_preflight.py — the launcher's pre-`compose up` safety checks.

Three failure classes it exists for:

* AUTH_JWT_SECRET unset: the gateway falls back to a generated secret in
  backend/.nova/.jwt_secret, and any time that file is unreadable or replaced
  every session dies on restart. Loud warning on dev, refusal on prod.
* Postgres max_connections below what the configured pools can open: the
  2026-11-01 outage exhausted all 100 connections.
* Root-owned files in the data dir (left by the root dev gateway) that the
  uid-1000 prod gateway cannot write: refusal on prod.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import stack_preflight as sp

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "stack_preflight.py"
GOOD_SECRET = "x" * 43


def _root(tmp_path: Path, *, env: str = "", config: str = "") -> Path:
    (tmp_path / ".env").write_text(env, encoding="utf-8")
    (tmp_path / "config.yaml").write_text(config, encoding="utf-8")
    (tmp_path / "backend" / ".nova").mkdir(parents=True)
    (tmp_path / "skills").mkdir()
    return tmp_path


# ── .env parsing ────────────────────────────────────────────────────────────


def test_read_dotenv_handles_quotes_comments_and_export(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\nA=1\nexport B=\"two\"\nC='3'\n\nD\n", encoding="utf-8")
    assert sp.read_dotenv(p) == {"A": "1", "B": "two", "C": "3"}


# ── JWT secret ──────────────────────────────────────────────────────────────


def test_missing_jwt_secret_warns_on_dev():
    r = sp.check_jwt_secret(stack="dev", environ={}, dotenv={})
    assert r.level == sp.WARN
    assert "AUTH_JWT_SECRET" in r.message


def test_missing_jwt_secret_refuses_prod():
    r = sp.check_jwt_secret(stack="prod", environ={}, dotenv={})
    assert r.level == sp.FAIL
    assert ".jwt_secret" in r.message  # tells the operator how to keep sessions


def test_short_jwt_secret_fails_everywhere():
    for stack in ("dev", "prod"):
        assert sp.check_jwt_secret(stack=stack, environ={}, dotenv={"AUTH_JWT_SECRET": "short"}).level == sp.FAIL


def test_jwt_secret_from_dotenv_or_environment_passes():
    assert sp.check_jwt_secret(stack="prod", environ={}, dotenv={"AUTH_JWT_SECRET": GOOD_SECRET}).level == sp.OK
    assert sp.check_jwt_secret(stack="prod", environ={"AUTH_JWT_SECRET": GOOD_SECRET}, dotenv={}).level == sp.OK


def test_secret_value_is_never_printed():
    r = sp.check_jwt_secret(stack="dev", environ={}, dotenv={"AUTH_JWT_SECRET": "tooshort-secret-value"})
    assert "tooshort-secret-value" not in r.message


# ── Postgres pool budget ────────────────────────────────────────────────────

PG_CONFIG = {"database": {"backend": "postgres", "pool_size": 5}, "checkpointer": {"type": "postgres"}}


def test_pool_demand_counts_every_process_and_every_pool():
    # per process: SQLAlchemy pool_size + max_overflow(10) + psycopg checkpointer pool (4)
    assert sp.pool_demand(PG_CONFIG, processes=1) == 5 + 10 + 4
    assert sp.pool_demand(PG_CONFIG, processes=3) == 3 * (5 + 10 + 4)


def test_pool_demand_is_zero_without_postgres():
    assert sp.pool_demand({"database": {"backend": "sqlite"}}, processes=4) == 0


def test_pool_budget_ok_when_it_fits():
    r = sp.check_pg_pool(PG_CONFIG, processes=2, max_connections=100, in_use=None)
    assert r.level == sp.OK


def test_pool_budget_warns_when_configured_pools_exceed_max_connections():
    cfg = {"database": {"backend": "postgres", "pool_size": 40}, "checkpointer": {"type": "postgres"}}
    r = sp.check_pg_pool(cfg, processes=2, max_connections=100, in_use=None)
    assert r.level == sp.WARN
    assert "max_connections" in r.message and "100" in r.message


def test_pool_budget_warns_when_live_usage_is_near_the_ceiling():
    r = sp.check_pg_pool(PG_CONFIG, processes=1, max_connections=100, in_use=92)
    assert r.level == sp.WARN
    assert "92" in r.message


def test_pool_budget_never_blocks():
    cfg = {"database": {"backend": "postgres", "pool_size": 500}}
    assert sp.check_pg_pool(cfg, processes=8, max_connections=100, in_use=99).level != sp.FAIL


# ── Ownership (prod runs as uid 1000) ───────────────────────────────────────


def test_foreign_owned_entry_is_found(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f").write_text("x", encoding="utf-8")
    me = os.getuid()
    assert sp.first_foreign_owned([tmp_path], uid=me) is None
    found = sp.first_foreign_owned([tmp_path], uid=me + 12345)
    assert found is not None


def test_ownership_refuses_prod_but_is_skipped_on_dev(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "first_foreign_owned", lambda paths, uid: tmp_path / "users" / "x")
    assert sp.check_ownership(stack="prod", paths=[tmp_path], uid=1000).level == sp.FAIL
    assert sp.check_ownership(stack="dev", paths=[tmp_path], uid=1000).level == sp.OK
    msg = sp.check_ownership(stack="prod", paths=[tmp_path], uid=1000).message
    assert "chown" in msg


# ── CLI ─────────────────────────────────────────────────────────────────────


def _run(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    e = {"PATH": os.environ["PATH"], "HOME": str(root)}
    e.update(env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--max-connections", "100", "--no-docker", "--prod-uid", str(os.getuid()), *args],
        env=e,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_dev_without_secret_warns_but_exits_zero(tmp_path):
    root = _root(tmp_path)
    p = _run(root, "--stack", "dev")
    assert p.returncode == 0, p.stderr
    assert "AUTH_JWT_SECRET" in p.stderr


def test_cli_prod_without_secret_exits_nonzero(tmp_path):
    root = _root(tmp_path)
    p = _run(root, "--stack", "prod")
    assert p.returncode == 1
    assert "REFUSING" in p.stderr


def test_cli_prod_with_secret_passes(tmp_path):
    root = _root(tmp_path, env=f"AUTH_JWT_SECRET={GOOD_SECRET}\n", config="database:\n  backend: postgres\n  pool_size: 5\n")
    p = _run(root, "--stack", "prod")
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("stack", ["dev", "prod"])
def test_cli_reports_the_pool_budget(tmp_path, stack):
    root = _root(tmp_path, env=f"AUTH_JWT_SECRET={GOOD_SECRET}\nGATEWAY_WORKERS=4\n", config="database:\n  backend: postgres\n  pool_size: 20\ncheckpointer:\n  type: postgres\n")
    p = _run(root, "--stack", stack, "--jobs-replicas", "1")
    assert p.returncode == 0
    # 5 processes x (20 + 10 + 4) = 170 > 100
    assert "170" in p.stderr and "max_connections" in p.stderr


def test_launcher_runs_preflight_before_compose_up_and_honours_its_refusal():
    text = (REPO_ROOT / "scripts" / "pm2-nova.sh").read_text(encoding="utf-8")
    pre = text.index("scripts/stack_preflight.py")
    up = text.index("exec /usr/bin/docker compose")
    assert pre < up
    block = text[pre:up]
    assert "exit 1" in block and '--stack "$NOVA_STACK"' in block
