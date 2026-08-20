#!/usr/bin/env python3
"""Copy Nova's application data from SQLite to Postgres.

Why this exists
---------------
SQLite reached 59.4 GB of unbounded LangGraph checkpoints and held the write
lock long enough that concurrent writers timed out — `database is locked`,
HTTP 500 on POST /api/threads, a 502 storm on /api/v1/auth/me. Retention fixes
the growth; Postgres removes the single-writer contention class outright.

`database.backend: postgres` is already first-class
(`config/database_config.py`), and the `postgres` extra already pins asyncpg,
langgraph-checkpoint-postgres and psycopg. This script only moves the rows.

What it does and does not move
------------------------------
It copies the application tables. It deliberately does NOT copy `checkpoints`
or `writes`:

  - They are LangGraph's own tables, created by its Postgres saver with a
    different schema than the SQLite saver's (jsonb vs BLOB, its own
    migration table). Copying rows between them would corrupt state.
  - Their contents are resumable-run scratch, not user data. The retention
    policy already discards all but the newest few per thread.

So threads, messages, users, runs, audit and credits survive; in-flight run
resume state does not. Any run mid-flight during the cutover must be re-sent —
which is why this is a maintenance-window operation, not a live migration.

Safety
------
- Refuses to run against a non-empty target unless --allow-nonempty, so a
  second accidental run cannot double-insert.
- Copies inside one transaction per table and verifies the row count matches
  the source before moving on; a mismatch aborts rather than half-migrating.
- Reads SQLite read-only. The source file is never modified, so rollback is
  "point config.yaml back at sqlite".

Usage:
    scripts/migrate-sqlite-to-postgres.py --dry-run
    scripts/migrate-sqlite-to-postgres.py --postgres-url postgresql://...
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE = REPO_ROOT / "backend" / ".deer-flow" / "data" / "deerflow.db"

#: LangGraph owns these; see the module docstring.
LANGGRAPH_TABLES = {"checkpoints", "writes"}

#: Alembic's bookkeeping. The target's own schema creation sets its version;
#: copying the source's would claim migrations that never ran there.
SKIP_TABLES = LANGGRAPH_TABLES | {"alembic_version"}


def log(message: str) -> None:
    print(message, flush=True)


def target_column_types(cur, table: str) -> dict[str, str]:
    """Map column -> Postgres data_type for one table."""
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s",
        (table,),
    )
    return {name: dtype for name, dtype in cur.fetchall()}


def coerce(value, pg_type: str):
    """Convert a SQLite value to what the Postgres column will accept.

    SQLite is dynamically typed and stores booleans as 0/1 integers, JSON as
    TEXT and timestamps as ISO strings. Postgres is strict, so a straight copy
    fails with e.g.

        column "consumed" is of type boolean but expression is of type smallint

    Coercing from the TARGET's declared types (rather than guessing from the
    source, which has none worth trusting) keeps this correct for every column
    without a hand-maintained list.
    """
    if value is None:
        return None
    if pg_type == "boolean":
        # SQLite yields 0/1; be tolerant of text forms too.
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "t", "yes"}
        return bool(value)
    if pg_type in {"json", "jsonb"} and isinstance(value, str):
        from psycopg.types.json import Json

        try:
            return Json(json.loads(value))
        except (ValueError, TypeError):
            return Json(value)
    return value


def sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows if r[0] not in SKIP_TABLES]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE)
    ap.add_argument(
        "--postgres-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="postgresql://user:pass@host:port/db (default: $DATABASE_URL)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--allow-nonempty",
        action="store_true",
        help="proceed even if the target already holds rows (double-insert risk)",
    )
    ap.add_argument("--batch", type=int, default=1000)
    args = ap.parse_args(argv)

    if not args.sqlite.exists():
        log(f"migrate: no SQLite database at {args.sqlite}")
        return 2
    if not args.postgres_url:
        log("migrate: no --postgres-url and no $DATABASE_URL")
        return 2

    try:
        import psycopg
    except ImportError:
        log(
            "migrate: psycopg is missing — install the `postgres` extra:\n"
            "  cd backend && uv sync --extra postgres"
        )
        return 2

    src = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    tables = sqlite_tables(src)

    log(f"source : {args.sqlite}")
    log(f"target : {args.postgres_url.split('@')[-1]}")
    log(f"tables : {len(tables)} to copy (skipping {', '.join(sorted(SKIP_TABLES))})\n")

    summary: dict[str, dict] = {}
    with psycopg.connect(args.postgres_url) as dst:
        for table in tables:
            source_rows = src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

            with dst.cursor() as cur:
                try:
                    cur.execute(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name=%s",
                        (table,),
                    )
                    exists = cur.fetchone()[0] == 1
                except psycopg.Error as exc:
                    log(f"  {table:<24} ERROR checking target: {exc}")
                    return 1

            if not exists:
                # The gateway creates its schema via Base.metadata.create_all on
                # first boot. Start it once against Postgres before running this.
                log(
                    f"  {table:<24} SKIP — not present in the target "
                    "(start the gateway against Postgres once to create the schema)"
                )
                summary[table] = {
                    "source": source_rows,
                    "copied": 0,
                    "status": "missing-target",
                }
                continue

            with dst.cursor() as cur:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                target_rows = cur.fetchone()[0]

            if target_rows and not args.allow_nonempty:
                log(
                    f"  {table:<24} REFUSING — target already has {target_rows:,} rows "
                    "(re-run with --allow-nonempty only if you mean it)"
                )
                return 1

            if args.dry_run:
                log(f"  {table:<24} would copy {source_rows:,} rows")
                summary[table] = {
                    "source": source_rows,
                    "copied": 0,
                    "status": "dry-run",
                }
                continue

            if source_rows == 0:
                log(f"  {table:<24} empty")
                summary[table] = {"source": 0, "copied": 0, "status": "ok"}
                continue

            columns = [d[1] for d in src.execute(f'PRAGMA table_info("{table}")')]
            with dst.cursor() as cur:
                types = target_column_types(cur, table)
            collist = ", ".join(f'"{c}"' for c in columns)
            placeholders = ", ".join(["%s"] * len(columns))
            col_types = [types.get(c, "") for c in columns]

            copied = 0
            cursor = src.execute(f'SELECT {collist} FROM "{table}"')
            with dst.cursor() as cur:
                while True:
                    rows = cursor.fetchmany(args.batch)
                    if not rows:
                        break
                    cur.executemany(
                        f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders})',
                        [
                            tuple(coerce(v, t) for v, t in zip(tuple(r), col_types))
                            for r in rows
                        ],
                    )
                    copied += len(rows)
            dst.commit()

            with dst.cursor() as cur:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                final = cur.fetchone()[0]

            # Verify before moving on. A half-migrated database that reports
            # success is worse than one that stopped loudly.
            if final != source_rows + target_rows:
                log(
                    f"  {table:<24} MISMATCH — source {source_rows:,}, "
                    f"target now {final:,}; aborting"
                )
                return 1

            log(f"  {table:<24} {copied:,} rows")
            summary[table] = {"source": source_rows, "copied": copied, "status": "ok"}

    log("\n" + json.dumps(summary, indent=2))
    total = sum(v["copied"] for v in summary.values())
    log(f"\ncopied {total:,} rows across {len(summary)} tables")
    if not args.dry_run:
        log(
            "\nNext: point config.yaml at postgres and restart.\n"
            "The SQLite file is untouched — rollback is switching database.backend back."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
