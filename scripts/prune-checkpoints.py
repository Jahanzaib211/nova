#!/usr/bin/env python3
"""Prune LangGraph checkpoint history from Nova's database.

Why this exists
---------------
LangGraph's checkpointer serialises the *entire* accumulated graph state on
every step, and nothing in the codebase ever deletes a checkpoint. On
2026-08-19 that produced a 59.4 GB SQLite file (57.3 GB of it `checkpoints`
blobs across 56,489 rows for just 140 threads) on a 97%-full disk. Writes to a
database that size hold the SQLite write lock long enough that concurrent
writers time out, which surfaced as `database is locked`, HTTP 500s on
`POST /api/threads`, and 502s on `/api/v1/auth/me`.

Retention policy
----------------
A checkpoint is KEPT if either:

  * it is one of the newest ``--keep-per-thread`` for its (thread, namespace) —
    this preserves a contiguous, resumable recent chain per thread; or
  * it is younger than ``--keep-days``.

Everything else is deleted, along with the `writes` rows that reference it.

Ordering by ``checkpoint_id`` is chronological: LangGraph mints them as UUIDv6,
whose 60-bit timestamp makes them lexicographically time-sortable. This module
decodes that timestamp directly, so the retention window needs no msgpack
deserialisation of tens of gigabytes. The decode is verified against the
``created_at`` field inside a row's own metadata by
``backend/tests/test_prune_checkpoints.py``.

Archiving
---------
A full blob archive of 57 GB compresses only ~2x (msgpack of already-compact
state), i.e. ~28 GB — which does not fit on the disk this script exists to
rescue. So the archive is split:

  * ``--manifest`` (always): every deleted row's identity, timestamp and byte
    size as gzipped JSONL. Small, and makes the deletion auditable.
  * ``--archive-blobs`` (default on): the full blob of the *newest deleted*
    checkpoint per (thread, namespace) only. That is one resumable state per
    old thread for a few hundred MB rather than tens of GB.

Usage
-----
    scripts/prune-checkpoints.py --dry-run
    scripts/prune-checkpoints.py --keep-per-thread 20 --keep-days 7 --vacuum

VACUUM takes an exclusive lock and rewrites the file, so it needs free space
roughly equal to the *resulting* database. Stop the gateway first.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import gzip
import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path

# UUIDv6 counts 100-nanosecond intervals from the Gregorian epoch.
_GREGORIAN_EPOCH = _dt.datetime(1582, 10, 15, tzinfo=_dt.timezone.utc)
_UNIX_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
_GREGORIAN_TO_UNIX_TICKS = int(
    (_UNIX_EPOCH - _GREGORIAN_EPOCH).total_seconds() * 10_000_000
)


def uuid6_unix_seconds(value: str) -> float | None:
    """Unix timestamp encoded in a UUIDv6, or None if not a parseable v6.

    Layout: ``time_high(32) - time_mid(16) - ver(4)+time_low(12)``. The version
    nibble sits in the high bits of the third group and must be masked off
    before the three fields are concatenated into the 60-bit tick count.
    """
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    if parsed.version != 6:
        return None
    packed = parsed.int
    time_high = (packed >> 96) & 0xFFFFFFFF
    time_mid = (packed >> 80) & 0xFFFF
    time_low = (packed >> 64) & 0x0FFF
    ticks = (time_high << 28) | (time_mid << 12) | time_low
    return (ticks - _GREGORIAN_TO_UNIX_TICKS) / 10_000_000


def default_db_path() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "backend" / ".deer-flow" / "data" / "deerflow.db"


def human_bytes(count: int) -> str:
    step = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(step) < 1024.0:
            return f"{step:.1f} {unit}"
        step /= 1024.0
    return f"{step:.1f} PB"


def db_bytes(conn: sqlite3.Connection) -> int:
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    return page_size * page_count


def build_doomed_set(
    conn: sqlite3.Connection, keep_per_thread: int, cutoff_epoch: float
) -> int:
    """Populate a temp table of checkpoints to delete. Returns the row count.

    Both retention rules are applied here rather than in Python because the
    candidate set is tens of thousands of rows wide and we only ever want to
    pull the identifying columns into memory, never the blobs.
    """
    conn.create_function("v6_epoch", 1, uuid6_unix_seconds, deterministic=True)
    conn.execute("DROP TABLE IF EXISTS temp.doomed")
    conn.execute(
        """
        CREATE TEMP TABLE doomed AS
        SELECT thread_id, checkpoint_ns, checkpoint_id
        FROM (
            SELECT thread_id,
                   checkpoint_ns,
                   checkpoint_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY thread_id, checkpoint_ns
                       ORDER BY checkpoint_id DESC
                   ) AS recency_rank
            FROM checkpoints
        )
        WHERE recency_rank > ?
          -- A NULL epoch means the id is not a decodable UUIDv6. Treat those as
          -- old rather than skipping them, otherwise malformed ids would be
          -- immortal; the recency_rank guard above still protects recent ones.
          AND COALESCE(v6_epoch(checkpoint_id), 0) < ?
        """,
        (keep_per_thread, cutoff_epoch),
    )
    conn.execute(
        "CREATE INDEX temp.doomed_idx ON doomed(thread_id, checkpoint_ns, checkpoint_id)"
    )
    return conn.execute("SELECT COUNT(*) FROM temp.doomed").fetchone()[0]


def measure(conn: sqlite3.Connection, exact: bool = False) -> dict:
    """Size of the doomed set.

    Exact byte totals need SUM(LENGTH(blob)) over every doomed row — a full read
    of tens of GB, minutes of IO on a loaded box, purely to print a number. The
    space actually reclaimed is already reported exactly by the before/after
    page-count delta, so exactness is opt-in via --measure-bytes. Otherwise the
    size is extrapolated from a bounded sample.
    """
    if not exact:
        avg = (
            conn.execute(
                """
            SELECT AVG(LENGTH(c.checkpoint) + LENGTH(COALESCE(c.metadata, '')))
            FROM (SELECT * FROM temp.doomed LIMIT 300) d
            JOIN checkpoints c
              ON c.thread_id = d.thread_id
             AND c.checkpoint_ns = d.checkpoint_ns
             AND c.checkpoint_id = d.checkpoint_id
            """
            ).fetchone()[0]
            or 0
        )
        rows = conn.execute("SELECT COUNT(*) FROM temp.doomed").fetchone()[0]
        writes_rows = conn.execute(
            """
            SELECT COUNT(*) FROM writes w JOIN temp.doomed d
              ON w.thread_id = d.thread_id
             AND w.checkpoint_ns = d.checkpoint_ns
             AND w.checkpoint_id = d.checkpoint_id
            """
        ).fetchone()[0]
        return {
            "checkpoint_bytes": int(avg * rows),
            "writes_rows": writes_rows,
            "writes_bytes": 0,
            "estimated": True,
        }

    checkpoint_bytes = conn.execute(
        """
        SELECT COALESCE(SUM(LENGTH(c.checkpoint) + LENGTH(COALESCE(c.metadata, ''))), 0)
        FROM checkpoints c JOIN temp.doomed d
          ON c.thread_id = d.thread_id
         AND c.checkpoint_ns = d.checkpoint_ns
         AND c.checkpoint_id = d.checkpoint_id
        """
    ).fetchone()[0]
    writes_rows, writes_bytes = conn.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(LENGTH(w.value)), 0)
        FROM writes w JOIN temp.doomed d
          ON w.thread_id = d.thread_id
         AND w.checkpoint_ns = d.checkpoint_ns
         AND w.checkpoint_id = d.checkpoint_id
        """
    ).fetchone()
    return {
        "checkpoint_bytes": checkpoint_bytes,
        "writes_rows": writes_rows,
        "writes_bytes": writes_bytes,
        "estimated": False,
    }


def write_manifest(conn: sqlite3.Connection, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        cursor = conn.execute(
            """
            SELECT c.thread_id, c.checkpoint_ns, c.checkpoint_id, c.parent_checkpoint_id,
                   c.type, LENGTH(c.checkpoint)
            FROM checkpoints c JOIN temp.doomed d
              ON c.thread_id = d.thread_id
             AND c.checkpoint_ns = d.checkpoint_ns
             AND c.checkpoint_id = d.checkpoint_id
            """
        )
        for thread_id, ns, cid, parent, ctype, size in cursor:
            epoch = uuid6_unix_seconds(cid)
            handle.write(
                json.dumps(
                    {
                        "thread_id": thread_id,
                        "checkpoint_ns": ns,
                        "checkpoint_id": cid,
                        "parent_checkpoint_id": parent,
                        "type": ctype,
                        "bytes": size,
                        "created_at": (
                            _dt.datetime.fromtimestamp(
                                epoch, _dt.timezone.utc
                            ).isoformat()
                            if epoch is not None
                            else None
                        ),
                    }
                )
                + "\n"
            )
            written += 1
    return written


def archive_blobs(conn: sqlite3.Connection, path: Path) -> tuple[int, int]:
    """Archive the newest deleted checkpoint per (thread, namespace).

    One resumable state per old thread. Archiving every deleted blob would cost
    ~28 GB compressed, which does not fit on the disk this script rescues.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = written = total = 0
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        cursor = conn.execute(
            """
            SELECT c.thread_id, c.checkpoint_ns, c.checkpoint_id, c.parent_checkpoint_id,
                   c.type, c.checkpoint, c.metadata
            FROM checkpoints c JOIN temp.doomed d
              ON c.thread_id = d.thread_id
             AND c.checkpoint_ns = d.checkpoint_ns
             AND c.checkpoint_id = d.checkpoint_id
            WHERE c.checkpoint_id = (
                SELECT MAX(d2.checkpoint_id) FROM temp.doomed d2
                WHERE d2.thread_id = c.thread_id AND d2.checkpoint_ns = c.checkpoint_ns
            )
            """
        )
        for thread_id, ns, cid, parent, ctype, blob, meta in cursor:
            rows += 1
            payload = {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": cid,
                "parent_checkpoint_id": parent,
                "type": ctype,
                "checkpoint_b64": base64.b64encode(blob or b"").decode("ascii"),
                "metadata_b64": base64.b64encode(meta or b"").decode("ascii"),
            }
            handle.write(json.dumps(payload) + "\n")
            written += 1
            total += len(blob or b"")
    return rows, total


def rebuild_tables(conn: sqlite3.Connection, progress=None) -> tuple[int, int]:
    """Rebuild `checkpoints`/`writes` keeping only the non-doomed rows.

    Row-by-row DELETE is pathologically slow here: a ~1 MB checkpoint blob
    occupies ~250 overflow pages, and freeing them one row at a time journals
    every page individually. Measured on this database it ran at roughly 5
    rows/sec — a three-hour job for 55k rows.

    Copying the survivors into a fresh table and DROPping the original inverts
    the cost: the write is proportional to what is KEPT (a few thousand rows),
    and the reclaim is a single bulk B-tree teardown. Same end state, minutes
    instead of hours.

    The new tables are created from the exact DDL of the originals, read back
    out of sqlite_master, so the schema (including the composite PRIMARY KEY
    that LangGraph's checkpointer relies on for upserts) cannot drift.
    """
    kept_checkpoints = kept_writes = 0

    for table in ("writes", "checkpoints"):
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]

        # Reuse the original DDL verbatim, only renaming the table it creates.
        #
        # The name must be matched with a regex, not a literal. After this
        # function's own ALTER TABLE ... RENAME, SQLite rewrites the stored DDL
        # with the name quoted -- `CREATE TABLE "writes" (` rather than
        # `CREATE TABLE writes (`. A literal replace silently fails to match on
        # the second run, the unmodified CREATE executes, and it dies with
        # 'table "writes" already exists'. Latent until the first re-prune.
        tmp = f"{table}__pruned"
        conn.execute(f"DROP TABLE IF EXISTS {tmp}")
        renamed, count = re.subn(
            # \b belongs only on the bare form: after a closing quote both
            # sides are non-word characters, so a word boundary cannot match
            # and the quoted alternative would never fire.
            rf'(CREATE\s+TABLE\s+)(?:"{table}"|`{table}`|\[{table}\]|{table}\b)',
            rf'\g<1>"{tmp}"',
            ddl,
            count=1,
            flags=re.IGNORECASE,
        )
        if count != 1:
            # Never execute a CREATE we failed to rewrite: it would recreate the
            # original table and destroy nothing, or error out mid-rebuild.
            raise RuntimeError(
                f"could not rewrite the table name in {table}'s DDL; refusing to "
                f"run it unchanged. DDL starts: {ddl[:80]!r}"
            )
        conn.execute(renamed)

        conn.execute(
            f"""
            INSERT INTO {tmp} SELECT * FROM {table} t
            WHERE NOT EXISTS (
                SELECT 1 FROM temp.doomed d
                WHERE d.thread_id = t.thread_id
                  AND d.checkpoint_ns = t.checkpoint_ns
                  AND d.checkpoint_id = t.checkpoint_id
            )
            """
        )
        kept = conn.execute(f"SELECT COUNT(*) FROM {tmp}").fetchone()[0]
        conn.commit()
        if progress:
            progress(table, kept)

        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        if table == "checkpoints":
            kept_checkpoints = kept
        else:
            kept_writes = kept

    return kept_checkpoints, kept_writes


def delete_doomed(
    conn: sqlite3.Connection, batch: int = 2000, progress=None
) -> tuple[int, int]:
    """Delete the doomed set in bounded batches.

    Deleting ~55 GB of blob rows in one transaction would grow the WAL to hold
    every modified page before it could be checkpointed back — tens of GB of
    temporary space on the very disk this script exists to free. Instead each
    batch is committed and the WAL is truncated back to zero, so peak extra
    space stays proportional to `batch`, not to the total deletion.

    `writes` rows are removed before their parent checkpoints so the table is
    never left referencing a checkpoint that no longer exists, even if the run
    is interrupted partway.
    """
    total_writes = total_checkpoints = 0

    while True:
        cur = conn.execute(
            """
            DELETE FROM writes WHERE rowid IN (
                SELECT w.rowid FROM writes w JOIN temp.doomed d
                  ON w.thread_id = d.thread_id
                 AND w.checkpoint_ns = d.checkpoint_ns
                 AND w.checkpoint_id = d.checkpoint_id
                LIMIT ?
            )
            """,
            (batch,),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if cur.rowcount <= 0:
            break
        total_writes += cur.rowcount
        if progress:
            progress("writes", total_writes)

    while True:
        cur = conn.execute(
            """
            DELETE FROM checkpoints WHERE rowid IN (
                SELECT c.rowid FROM checkpoints c JOIN temp.doomed d
                  ON c.thread_id = d.thread_id
                 AND c.checkpoint_ns = d.checkpoint_ns
                 AND c.checkpoint_id = d.checkpoint_id
                LIMIT ?
            )
            """,
            (batch,),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if cur.rowcount <= 0:
            break
        total_checkpoints += cur.rowcount
        if progress:
            progress("checkpoints", total_checkpoints)

    return total_checkpoints, total_writes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", type=Path, default=default_db_path())
    parser.add_argument(
        "--keep-per-thread",
        type=int,
        default=20,
        help="newest N checkpoints to keep per (thread, namespace)",
    )
    parser.add_argument(
        "--keep-days",
        type=float,
        default=7.0,
        help="keep everything younger than this many days",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="where manifests/blob archives go (default <db dir>/checkpoint-archive)",
    )
    parser.add_argument(
        "--no-archive-blobs",
        action="store_true",
        help="write only the manifest, no blob archive",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="VACUUM after deleting (needs an exclusive lock and free space)",
    )
    parser.add_argument(
        "--measure-bytes",
        action="store_true",
        help="exact byte totals (reads every doomed blob; slow)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=2000,
        help="rows deleted per committed batch (bounds WAL growth)",
    )
    parser.add_argument(
        "--strategy",
        choices=("rebuild", "delete"),
        default="rebuild",
        help="rebuild: copy survivors into a fresh table and drop the "
        "original (fast). delete: row-by-row DELETE (slow, but "
        "leaves the table object identity untouched).",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable summary"
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"prune-checkpoints: no database at {args.db}", file=sys.stderr)
        return 2

    archive_dir = args.archive_dir or args.db.parent / "checkpoint-archive"
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cutoff = _dt.datetime.now(_dt.timezone.utc).timestamp() - args.keep_days * 86400

    conn = sqlite3.connect(args.db)
    conn.row_factory = None
    try:
        conn.execute("PRAGMA busy_timeout = 60000")

        before_bytes = db_bytes(conn)
        before_checkpoints = conn.execute(
            "SELECT COUNT(*) FROM checkpoints"
        ).fetchone()[0]
        before_writes = conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0]

        doomed = build_doomed_set(conn, args.keep_per_thread, cutoff)
        sizes = measure(conn, exact=args.measure_bytes)

        summary = {
            "db": str(args.db),
            "dry_run": args.dry_run,
            "keep_per_thread": args.keep_per_thread,
            "keep_days": args.keep_days,
            "cutoff_utc": _dt.datetime.fromtimestamp(
                cutoff, _dt.timezone.utc
            ).isoformat(),
            "before": {
                "db_bytes": before_bytes,
                "checkpoints": before_checkpoints,
                "writes": before_writes,
            },
            "doomed": {
                "checkpoints": doomed,
                "checkpoint_bytes": sizes["checkpoint_bytes"],
                "writes": sizes["writes_rows"],
                "writes_bytes": sizes["writes_bytes"],
            },
        }

        if args.dry_run:
            summary["result"] = "dry-run: nothing written"
        elif doomed == 0:
            summary["result"] = "nothing to prune"
        else:
            manifest_path = archive_dir / f"manifest-{stamp}.jsonl.gz"
            summary["manifest"] = str(manifest_path)
            summary["manifest_rows"] = write_manifest(conn, manifest_path)

            if not args.no_archive_blobs:
                blob_path = archive_dir / f"blobs-{stamp}.jsonl.gz"
                rows, raw = archive_blobs(conn, blob_path)
                summary["blob_archive"] = str(blob_path)
                summary["blob_archive_rows"] = rows
                summary["blob_archive_raw_bytes"] = raw
                summary["blob_archive_file_bytes"] = blob_path.stat().st_size

            def _tick(kind: str, n: int) -> None:
                print(f"  ... deleted {n:,} {kind}", file=sys.stderr, flush=True)

            tick = None if args.json else _tick
            if args.strategy == "rebuild":
                kept_c, kept_w = rebuild_tables(conn, progress=tick)
                deleted_checkpoints = before_checkpoints - kept_c
                deleted_writes = before_writes - kept_w
            else:
                deleted_checkpoints, deleted_writes = delete_doomed(
                    conn, batch=args.batch, progress=tick
                )
            conn.commit()
            summary["deleted"] = {
                "checkpoints": deleted_checkpoints,
                "writes": deleted_writes,
            }

            if args.vacuum:
                conn.isolation_level = None
                conn.execute("VACUUM")
                summary["vacuumed"] = True

            orphans = conn.execute(
                """
                DELETE FROM writes WHERE NOT EXISTS (
                    SELECT 1 FROM checkpoints c
                    WHERE c.thread_id = writes.thread_id
                      AND c.checkpoint_ns = writes.checkpoint_ns
                      AND c.checkpoint_id = writes.checkpoint_id
                )
                """
            ).rowcount
            conn.commit()
            summary["orphaned_writes_removed"] = max(orphans, 0)

            summary["after"] = {
                "db_bytes": db_bytes(conn),
                "checkpoints": conn.execute(
                    "SELECT COUNT(*) FROM checkpoints"
                ).fetchone()[0],
                "writes": conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0],
            }
            summary["integrity_check"] = conn.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
            summary["result"] = (
                "pruned"
                if summary["integrity_check"] == "ok"
                else "pruned-with-integrity-error"
            )
    finally:
        conn.close()

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        b = summary["before"]
        d = summary["doomed"]
        print(f"database        : {summary['db']}")
        print(
            f"retention       : keep newest {args.keep_per_thread}/thread, or younger than {args.keep_days}d"
        )
        print(
            f"before          : {human_bytes(b['db_bytes'])}  "
            f"({b['checkpoints']:,} checkpoints, {b['writes']:,} writes)"
        )
        print(
            f"to delete       : {d['checkpoints']:,} checkpoints ({human_bytes(d['checkpoint_bytes'])}), "
            f"{d['writes']:,} writes ({human_bytes(d['writes_bytes'])})"
        )
        if "after" in summary:
            a = summary["after"]
            print(
                f"after           : {human_bytes(a['db_bytes'])}  "
                f"({a['checkpoints']:,} checkpoints, {a['writes']:,} writes)"
            )
            print(f"reclaimed       : {human_bytes(b['db_bytes'] - a['db_bytes'])}")
        if "manifest" in summary:
            print(
                f"manifest        : {summary['manifest']} ({summary['manifest_rows']:,} rows)"
            )
        if "blob_archive" in summary:
            print(
                f"blob archive    : {summary['blob_archive']} "
                f"({summary['blob_archive_rows']:,} threads, "
                f"{human_bytes(summary['blob_archive_file_bytes'])})"
            )
        print(f"result          : {summary['result']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
