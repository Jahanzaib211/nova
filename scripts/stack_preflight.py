#!/usr/bin/env python3
"""Pre-`compose up` safety checks for scripts/pm2-nova.sh.

Run by the launcher before it execs ``docker compose up``; also safe to run by
hand (read-only: it reads .env and config.yaml, stats files, and at most runs
two ``SHOW``/``SELECT`` statements through ``docker exec`` on the Postgres
container):

    python3 scripts/stack_preflight.py --stack prod

Checks:

1. ``AUTH_JWT_SECRET``. Unset, the gateway signs sessions with a secret it
   generates and persists to ``backend/.nova/.jwt_secret``; whenever that file
   is unreadable by the gateway's user or replaced, every session dies on the
   next restart. dev: loud warning. prod: refusal (exit 1). A secret shorter
   than 32 bytes fails on both, because the gateway refuses it at import.
2. Postgres ``max_connections`` versus what the configured pools can open
   (the 2026-11-01 outage exhausted all 100). Warning only, never a refusal: a
   heuristic must not keep a stack from coming back up.
3. prod only: entries under the data dir / skills not owned by the uid the
   prod gateway runs as. The dev gateway runs as root and leaves root-owned
   per-user directories behind; the uid-1000 prod gateway then fails writing
   them. Refusal, with the chown that fixes it.

Exit status: 0 = start the stack (possibly with warnings), 1 = refuse.
Secret values are never printed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "fail"

# Mirrors app/gateway/auth/config.py (_MIN_SECRET_BYTES, RFC 7518 §3.2).
MIN_SECRET_BYTES = 32
# SQLAlchemy's QueuePool default max_overflow; nova.persistence.engine does
# not override it for postgres, so each process can open pool_size + 10.
SQLALCHEMY_MAX_OVERFLOW = 10
# psycopg_pool.AsyncConnectionPool defaults (min_size=4, max_size=min_size);
# nova.runtime.checkpointer.async_provider builds one per process.
CHECKPOINTER_POOL = 4
# Postgres' superuser_reserved_connections default, plus room for psql, the
# schema step, the healthcheck daemon and the gates.
RESERVED = 3
HEADROOM = 10
# Warn when the live connection count is this close to the ceiling.
LIVE_USAGE_RATIO = 0.8
DEFAULT_MAX_CONNECTIONS = 100  # Postgres default; the compose files set none.


@dataclass
class Result:
    name: str
    level: str
    message: str


def read_dotenv(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser (no interpolation), enough for .env files."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


# ── 1. JWT secret ───────────────────────────────────────────────────────────


def check_jwt_secret(*, stack: str, environ: dict[str, str], dotenv: dict[str, str]) -> Result:
    secret = environ.get("AUTH_JWT_SECRET") or dotenv.get("AUTH_JWT_SECRET") or ""
    if secret:
        n = len(secret.encode())
        if n < MIN_SECRET_BYTES:
            return Result("jwt_secret", FAIL, f"AUTH_JWT_SECRET is {n} bytes; the gateway refuses anything under {MIN_SECRET_BYTES}. Generate one: python3 -c 'import secrets; print(secrets.token_urlsafe(32))'")
        return Result("jwt_secret", OK, "AUTH_JWT_SECRET is set")
    remedy = "add it to .env — to keep existing sessions reuse the persisted one: printf 'AUTH_JWT_SECRET=%s\\n' \"$(cat backend/.nova/.jwt_secret)\" >> .env"
    if stack == "prod":
        return Result("jwt_secret", FAIL, f"AUTH_JWT_SECRET is not set. The prod gateway (uid 1000) would fall back to backend/.nova/.jwt_secret, and sessions die on every restart whenever it cannot read that file; {remedy}")
    return Result("jwt_secret", WARN, f"AUTH_JWT_SECRET is not set — sessions survive restarts only while backend/.nova/.jwt_secret stays readable by the gateway; {remedy}")


# ── 2. Postgres pool budget ─────────────────────────────────────────────────


def _uses_postgres(cfg: dict) -> bool:
    return str((cfg.get("database") or {}).get("backend", "")).lower() == "postgres"


def _checkpointer_pool(cfg: dict) -> int:
    cp = cfg.get("checkpointer")
    if isinstance(cp, dict) and cp.get("type"):
        return CHECKPOINTER_POOL if str(cp["type"]).lower() == "postgres" else 0
    # No checkpointer section: it follows the database backend.
    return CHECKPOINTER_POOL if _uses_postgres(cfg) else 0


def pool_demand(cfg: dict, *, processes: int) -> int:
    """Connections the configured pools can open across ``processes`` processes."""
    per = 0
    if _uses_postgres(cfg):
        pool_size = int((cfg.get("database") or {}).get("pool_size") or 5)
        per += pool_size + SQLALCHEMY_MAX_OVERFLOW
    per += _checkpointer_pool(cfg)
    if per == 0:
        return 0
    store = cfg.get("store")
    if isinstance(store, dict) and str(store.get("type", "")).lower() == "postgres":
        per += 1
    return per * processes


def check_pg_pool(cfg: dict, *, processes: int, max_connections: int, in_use: int | None) -> Result:
    demand = pool_demand(cfg, processes=processes)
    if demand == 0:
        return Result("pg_pool", OK, "no Postgres pools configured")
    need = demand + RESERVED + HEADROOM
    problems = []
    if need > max_connections:
        problems.append(
            f"configured pools can open {demand} connections ({processes} process(es) x per-process pools; database.pool_size + {SQLALCHEMY_MAX_OVERFLOW} overflow + checkpointer {CHECKPOINTER_POOL}) "
            f"+ {RESERVED + HEADROOM} reserved/headroom = {need} > max_connections {max_connections}. "
            "Lower database.pool_size / GATEWAY_WORKERS, or raise max_connections on the postgres service"
        )
    if in_use is not None and in_use >= LIVE_USAGE_RATIO * max_connections:
        problems.append(f"Postgres already has {in_use} of max_connections {max_connections} connections open — look for a leak before restarting into it")
    if problems:
        return Result("pg_pool", WARN, "; ".join(problems))
    usage = f", {in_use} open now" if in_use is not None else ""
    return Result("pg_pool", OK, f"pools need {need} of max_connections {max_connections}{usage}")


def _psql_scalar(container: str, sql: str) -> str | None:
    cmd = ["docker", "exec", container, "sh", "-c", f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "{sql}"']
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def live_postgres_stats(container: str) -> tuple[int | None, int | None]:
    def _int(v: str | None) -> int | None:
        try:
            return int(v) if v is not None else None
        except ValueError:
            return None

    return _int(_psql_scalar(container, "show max_connections")), _int(_psql_scalar(container, "select count(*) from pg_stat_activity"))


# ── 3. Ownership ────────────────────────────────────────────────────────────


def first_foreign_owned(paths: list[Path], uid: int) -> Path | None:
    """First path (the roots included) not owned by ``uid``; None if all are."""
    for root in paths:
        try:
            if root.lstat().st_uid != uid:
                return root
        except OSError:
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            for name in (*dirnames, *filenames):
                p = Path(dirpath) / name
                try:
                    if p.lstat().st_uid != uid:
                        return p
                except OSError:
                    continue
    return None


def check_ownership(*, stack: str, paths: list[Path], uid: int) -> Result:
    if stack != "prod":
        return Result("ownership", OK, "dev gateway runs as root; ownership not checked")
    bad = first_foreign_owned(paths, uid)
    if bad is None:
        return Result("ownership", OK, f"data dir and skills are owned by uid {uid}")
    targets = " ".join(str(p) for p in paths)
    return Result(
        "ownership",
        FAIL,
        f"{bad} is not owned by uid {uid}, which the prod gateway runs as — it cannot write there (left behind by the root dev gateway). Fix: sudo chown -R {uid}:{uid} {targets}",
    )


# ── CLI ─────────────────────────────────────────────────────────────────────


def _load_config(path: Path) -> dict:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _report(results: list[Result], stack: str) -> int:
    failed = [r for r in results if r.level == FAIL]
    for r in results:
        if r.level == OK:
            print(f"[preflight] ok    {r.name}: {r.message}", file=sys.stderr)
    for r in results:
        if r.level == WARN:
            bar = "!" * 78
            print(f"[preflight] {bar}\n[preflight] WARNING {r.name}: {r.message}\n[preflight] {bar}", file=sys.stderr)
    for r in failed:
        print(f"[preflight] FAIL  {r.name}: {r.message}", file=sys.stderr)
    if failed:
        print(f"[preflight] REFUSING to start NOVA_STACK={stack}: {len(failed)} check(s) failed (see above).", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.environ.get("NOVA_ROOT") or str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--stack", choices=("dev", "prod"), default=os.environ.get("NOVA_STACK") or "dev")
    ap.add_argument("--data-dirname", default=os.environ.get("NOVA_DATA_DIRNAME") or ".nova")
    ap.add_argument("--container-prefix", default=os.environ.get("NOVA_CONTAINER_PREFIX") or "nova")
    ap.add_argument("--jobs-replicas", type=int, default=1)
    ap.add_argument("--gateway-workers", type=int, default=None, help="default: GATEWAY_WORKERS from the environment/.env, else 1")
    ap.add_argument("--max-connections", type=int, default=None, help="default: read from the running Postgres, else 100")
    ap.add_argument("--no-docker", action="store_true", help="never call docker (tests, offline)")
    ap.add_argument("--prod-uid", type=int, default=1000, help="uid the prod gateway runs as (compose `user:`)")
    args = ap.parse_args(argv)

    root = Path(args.root)
    dotenv = read_dotenv(root / ".env")
    cfg = _load_config(root / "config.yaml")

    workers = args.gateway_workers
    if workers is None:
        try:
            workers = int(os.environ.get("GATEWAY_WORKERS") or dotenv.get("GATEWAY_WORKERS") or 1)
        except ValueError:
            workers = 1
    processes = max(workers, 1) + max(args.jobs_replicas, 0)

    max_conn, in_use = args.max_connections, None
    if not args.no_docker:
        live_max, in_use = live_postgres_stats(f"{args.container_prefix}-postgres")
        if max_conn is None:
            max_conn = live_max
    if max_conn is None:
        max_conn = DEFAULT_MAX_CONNECTIONS

    results = [
        check_jwt_secret(stack=args.stack, environ=dict(os.environ), dotenv=dotenv),
        check_pg_pool(cfg, processes=processes, max_connections=max_conn, in_use=in_use),
        check_ownership(stack=args.stack, paths=[root / "backend" / args.data_dirname, root / "skills"], uid=args.prod_uid),
    ]
    return _report(results, args.stack)


if __name__ == "__main__":
    sys.exit(main())
