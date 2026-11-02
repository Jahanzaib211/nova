"""Checkpoint retention: delete old LangGraph checkpoints, consistently and in batches.

Why this exists
---------------
LangGraph writes one checkpoint per graph step and never deletes one. With the
Postgres saver every step that changes ``messages`` also writes a fresh blob of
the *whole* message list, so one long thread costs O(steps x history). On
2026-11-02 ``checkpoint_blobs`` was 7.8 GB of an ~8 GB database: 3 265
checkpoints and 4 GB of blobs belonged to a single thread.

``scripts/prune-checkpoints.py`` already prunes from the host, but it is a
host-side tool driven by the gates daemon and keyed on checkpoint age. This
module is the in-process job: it runs from the jobs worker
(``maintenance.checkpoints.prune``) or ``python -m app.jobs.checkpoint_retention``,
speaks both engines through the app's own SQLAlchemy engine, and adds the
guarantees a background job needs: batch-limited, dry-run by default, and never
touching a thread with a run in flight.

Retention policy
----------------
For each ``(thread_id, checkpoint_ns)`` the newest ``keep_per_thread``
checkpoints are kept. A thread is left **entirely** untouched when

* its newest checkpoint is younger than ``keep_active_days``, or
* it has a run in status ``pending`` / ``running`` (re-checked inside every
  delete transaction, not only when the plan is built).

``checkpoint_id`` is a UUIDv6, whose leading 60-bit timestamp makes the string
order chronological, so recency is a plain ``ORDER BY`` and the activity
window a string comparison -- no checkpoint is ever deserialised.

What is deleted, per batch, in one transaction
----------------------------------------------
1. ``checkpoint_writes`` / ``writes`` rows of the doomed checkpoints (keyed by
   ``checkpoint_id``, so an exact match).
2. The doomed ``checkpoints`` rows.
3. Postgres only: ``checkpoint_blobs`` is keyed
   ``(thread_id, checkpoint_ns, channel, version)`` -- it has no
   ``checkpoint_id`` (the column the host script once joined on, which aborted
   every run; pinned by ``tests/test_prune_checkpoints_schema.py``). A blob is
   garbage when **no surviving** checkpoint of its ``(thread, ns)`` names its
   ``(channel, version)`` in ``channel_versions`` **and** its version is older
   than the newest version a survivor names for that channel. The second
   condition keeps a blob written by a step whose checkpoint row has not been
   committed yet out of range: its version is newer than anything a survivor
   references. Only zero-padded versions (LangGraph's
   ``"<32 digits>.<hash>"`` format) are compared, so lexical order is numeric.

SQLite stores channel values inline in ``checkpoints``, so steps 1-2 are the
whole job there. Deleting rows frees pages for reuse; returning bytes to the OS
needs ``VACUUM`` (SQLite) or ``VACUUM FULL`` / ``pg_repack`` (Postgres), which
take exclusive locks and are deliberately left to a maintenance window.
"""

from __future__ import annotations

import datetime as _dt
import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger(__name__)

#: Run statuses that mean "a worker may be writing this thread's checkpoints".
ACTIVE_RUN_STATUSES = ("pending", "running")

# UUIDv6 counts 100-nanosecond intervals from the Gregorian epoch.
_GREGORIAN_TO_UNIX_TICKS = int((_dt.datetime(1970, 1, 1, tzinfo=_dt.UTC) - _dt.datetime(1582, 10, 15, tzinfo=_dt.UTC)).total_seconds() * 10_000_000)

#: Sorts after every hex string: used as the activity cutoff when the window is
#: disabled, so no thread counts as active.
_NEVER = "~"


def uuid6_for_epoch(epoch: float) -> str:
    """Smallest UUIDv6 at ``epoch``, for string comparison against checkpoint_id."""
    ticks = int(epoch * 10_000_000) + _GREGORIAN_TO_UNIX_TICKS
    return f"{(ticks >> 28) & 0xFFFFFFFF:08x}-{(ticks >> 12) & 0xFFFF:04x}-6{ticks & 0x0FFF:03x}-0000-000000000000"


def uuid6_unix_seconds(value: str) -> float | None:
    """Unix time encoded in a UUIDv6 ``checkpoint_id``, or None if it is not one."""
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    if parsed.version != 6:
        return None
    packed = parsed.int
    ticks = (((packed >> 96) & 0xFFFFFFFF) << 28) | (((packed >> 80) & 0xFFFF) << 12) | ((packed >> 64) & 0x0FFF)
    return (ticks - _GREGORIAN_TO_UNIX_TICKS) / 10_000_000


@dataclass
class RetentionPolicy:
    keep_per_thread: int = 20
    keep_active_days: float = 7.0
    batch_size: int = 500
    max_batches: int = 50

    def __post_init__(self) -> None:
        if self.keep_per_thread < 1:
            raise ValueError("keep_per_thread must be >= 1: the newest checkpoint is what a thread resumes from")
        if self.batch_size < 1 or self.max_batches < 1:
            raise ValueError("batch_size and max_batches must be >= 1")

    @classmethod
    def from_config(cls, cfg: Any) -> RetentionPolicy:
        return cls(
            keep_per_thread=cfg.keep_per_thread,
            keep_active_days=cfg.keep_active_days,
            batch_size=cfg.batch_size,
            max_batches=cfg.max_batches,
        )

    def active_cutoff(self, now: float | None = None) -> str:
        if self.keep_active_days <= 0:
            return _NEVER
        now = _dt.datetime.now(_dt.UTC).timestamp() if now is None else now
        return uuid6_for_epoch(now - self.keep_active_days * 86400)


@dataclass
class RetentionReport:
    backend: str
    dry_run: bool
    policy: dict[str, Any]
    checkpoints_total: int = 0
    threads_total: int = 0
    protected_threads: int = 0
    doomed_checkpoints: int = 0
    doomed_writes: int = 0
    reclaimable_bytes: int = 0
    deleted_checkpoints: int = 0
    deleted_writes: int = 0
    deleted_blobs: int = 0
    batches: int = 0
    complete: bool = True
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Tables:
    checkpoints: str
    writes: str
    blobs: str | None


_POSTGRES = _Tables("checkpoints", "checkpoint_writes", "checkpoint_blobs")
_SQLITE = _Tables("checkpoints", "writes", None)


def _doomed_cte(tables: _Tables, *, with_runs: bool) -> str:
    """CTEs ending in ``doomed(thread_id, checkpoint_ns, checkpoint_id)``.

    Portable SQL (window functions work on SQLite >= 3.25 and Postgres).
    ``NOT EXISTS`` rather than ``NOT IN``: a NULL thread_id in ``runs`` would
    make ``NOT IN`` reject every row.
    """
    runs_clause = (
        f"""
           OR EXISTS (SELECT 1 FROM runs r
                      WHERE r.thread_id = tn.thread_id
                        AND r.status IN ({", ".join(f"'{s}'" for s in ACTIVE_RUN_STATUSES)}))"""
        if with_runs
        else ""
    )
    return f"""
WITH thread_newest AS (
    SELECT thread_id, MAX(checkpoint_id) AS newest
    FROM {tables.checkpoints}
    GROUP BY thread_id
),
protected AS (
    SELECT tn.thread_id FROM thread_newest tn
    WHERE tn.newest >= :active_cutoff{runs_clause}
),
ranked AS (
    SELECT c.thread_id, c.checkpoint_ns, c.checkpoint_id,
           ROW_NUMBER() OVER (PARTITION BY c.thread_id, c.checkpoint_ns ORDER BY c.checkpoint_id DESC) AS recency_rank
    FROM {tables.checkpoints} c
    WHERE NOT EXISTS (SELECT 1 FROM protected p WHERE p.thread_id = c.thread_id)
),
doomed AS (
    SELECT thread_id, checkpoint_ns, checkpoint_id FROM ranked WHERE recency_rank > :keep
)
"""


#: Postgres blob GC for one (thread, ns), run after that pair's doomed
#: checkpoints are deleted in the same transaction. See the module docstring
#: for why both conditions are required.
_PG_BLOB_GC_ONE = """
WITH survivor_versions AS (
    SELECT kv.key AS channel, kv.value #>> '{}' AS version
    FROM checkpoints c
    CROSS JOIN LATERAL jsonb_each(c.checkpoint->'channel_versions') AS kv
    WHERE c.thread_id = :thread_id AND c.checkpoint_ns = :checkpoint_ns
),
channel_max AS (
    SELECT channel, MAX(version) AS max_version FROM survivor_versions GROUP BY channel
)
DELETE FROM checkpoint_blobs b
WHERE b.thread_id = :thread_id
  AND b.checkpoint_ns = :checkpoint_ns
  AND b.version ~ '^[0-9]{32}'
  AND b.version < (SELECT cm.max_version FROM channel_max cm WHERE cm.channel = b.channel)
  AND NOT EXISTS (
        SELECT 1 FROM survivor_versions sv
        WHERE sv.channel = b.channel AND sv.version = b.version
  )
"""

#: The same GC as a size estimate over every doomed checkpoint at once.
_PG_BLOB_ESTIMATE_TAIL = """
, survivor_versions AS (
    SELECT c.thread_id, c.checkpoint_ns, kv.key AS channel, kv.value #>> '{}' AS version
    FROM checkpoints c
    CROSS JOIN LATERAL jsonb_each(c.checkpoint->'channel_versions') AS kv
    WHERE EXISTS (SELECT 1 FROM doomed d WHERE d.thread_id = c.thread_id AND d.checkpoint_ns = c.checkpoint_ns)
      AND NOT EXISTS (
            SELECT 1 FROM doomed d
            WHERE d.thread_id = c.thread_id AND d.checkpoint_ns = c.checkpoint_ns AND d.checkpoint_id = c.checkpoint_id
      )
),
channel_max AS (
    SELECT thread_id, checkpoint_ns, channel, MAX(version) AS max_version
    FROM survivor_versions GROUP BY thread_id, checkpoint_ns, channel
)
SELECT COUNT(*), COALESCE(SUM(pg_column_size(b.blob)), 0)
FROM checkpoint_blobs b
JOIN channel_max cm
  ON cm.thread_id = b.thread_id AND cm.checkpoint_ns = b.checkpoint_ns AND cm.channel = b.channel
WHERE b.version ~ '^[0-9]{32}'
  AND b.version < cm.max_version
  AND NOT EXISTS (
        SELECT 1 FROM survivor_versions sv
        WHERE sv.thread_id = b.thread_id AND sv.checkpoint_ns = b.checkpoint_ns
          AND sv.channel = b.channel AND sv.version = b.version
  )
"""


async def _table_names(conn: AsyncConnection) -> set[str]:
    def _names(sync_conn) -> set[str]:
        from sqlalchemy import inspect

        return set(inspect(sync_conn).get_table_names())

    return await conn.run_sync(_names)


def _tables_for(dialect: str) -> _Tables:
    if dialect == "postgresql":
        return _POSTGRES
    if dialect == "sqlite":
        return _SQLITE
    raise ValueError(f"checkpoint retention does not support the {dialect!r} dialect")


async def prune_checkpoints(engine: AsyncEngine, policy: RetentionPolicy, *, dry_run: bool = True, now: float | None = None) -> RetentionReport:
    """Apply ``policy`` to the checkpoint tables behind ``engine``.

    With ``dry_run`` (the default) only SELECTs are issued and the report
    carries the estimate. Otherwise at most ``policy.max_batches`` batches of
    ``policy.batch_size`` checkpoints are deleted, each in its own transaction;
    ``report.complete`` is False when work remains for the next sweep.
    """
    dialect = engine.dialect.name
    tables = _tables_for(dialect)
    report = RetentionReport(backend=dialect, dry_run=dry_run, policy=asdict(policy))

    async with engine.connect() as conn:
        names = await _table_names(conn)
    if tables.checkpoints not in names:
        report.notes.append(f"no {tables.checkpoints} table: nothing to prune")
        return report
    has_writes = tables.writes in names
    has_blobs = tables.blobs is not None and tables.blobs in names
    with_runs = "runs" in names
    if not with_runs:
        report.notes.append("no runs table: running-run protection unavailable, relying on the activity window only")

    cte = _doomed_cte(tables, with_runs=with_runs)
    params = {"keep": policy.keep_per_thread, "active_cutoff": policy.active_cutoff(now)}

    async with engine.connect() as conn:
        row = (await conn.execute(text(f"SELECT COUNT(*), COUNT(DISTINCT thread_id) FROM {tables.checkpoints}"))).one()
        report.checkpoints_total, report.threads_total = int(row[0]), int(row[1])
        report.protected_threads = int((await conn.execute(text(cte + "SELECT COUNT(*) FROM protected"), params)).scalar_one())
        report.doomed_checkpoints = int((await conn.execute(text(cte + "SELECT COUNT(*) FROM doomed"), params)).scalar_one())
        if report.doomed_checkpoints and has_writes:
            report.doomed_writes = int(
                (
                    await conn.execute(
                        text(cte + f"SELECT COUNT(*) FROM {tables.writes} w JOIN doomed d ON d.thread_id = w.thread_id AND d.checkpoint_ns = w.checkpoint_ns AND d.checkpoint_id = w.checkpoint_id"),
                        params,
                    )
                ).scalar_one()
            )
        if report.doomed_checkpoints:
            report.reclaimable_bytes = await _estimate_bytes(conn, dialect, tables, cte, params, has_writes=has_writes, has_blobs=has_blobs, report=report)

    if dry_run or report.doomed_checkpoints == 0:
        return report

    for _ in range(policy.max_batches):
        deleted = await _delete_one_batch(engine, tables, cte, params, policy.batch_size, has_writes=has_writes, has_blobs=has_blobs, with_runs=with_runs, report=report)
        if deleted == 0:
            break
        report.batches += 1
    else:
        async with engine.connect() as conn:
            remaining = int((await conn.execute(text(cte + "SELECT COUNT(*) FROM doomed"), params)).scalar_one())
        report.complete = remaining == 0
        if remaining:
            report.notes.append(f"{remaining} checkpoints left for the next sweep (max_batches reached)")

    logger.info(
        "checkpoint retention: deleted %d checkpoints, %d writes, %d blobs in %d batches (%s)",
        report.deleted_checkpoints,
        report.deleted_writes,
        report.deleted_blobs,
        report.batches,
        dialect,
    )
    return report


async def _estimate_bytes(conn: AsyncConnection, dialect: str, tables: _Tables, cte: str, params: dict, *, has_writes: bool, has_blobs: bool, report: RetentionReport) -> int:
    """Logical bytes the doomed rows hold. Not file bytes: see the module docstring."""
    join = "JOIN doomed d ON d.thread_id = {a}.thread_id AND d.checkpoint_ns = {a}.checkpoint_ns AND d.checkpoint_id = {a}.checkpoint_id"
    if dialect == "postgresql":
        size_ckpt = "pg_column_size(c.checkpoint) + pg_column_size(c.metadata)"
        size_write = "pg_column_size(w.blob)"
    else:
        size_ckpt = "LENGTH(c.checkpoint) + COALESCE(LENGTH(c.metadata), 0)"
        size_write = "COALESCE(LENGTH(w.value), 0)"
    total = int((await conn.execute(text(cte + f"SELECT COALESCE(SUM({size_ckpt}), 0) FROM {tables.checkpoints} c " + join.format(a="c")), params)).scalar_one())
    if has_writes:
        total += int((await conn.execute(text(cte + f"SELECT COALESCE(SUM({size_write}), 0) FROM {tables.writes} w " + join.format(a="w")), params)).scalar_one())
    if has_blobs:
        count, blob_bytes = (await conn.execute(text(cte + _PG_BLOB_ESTIMATE_TAIL), params)).one()
        report.notes.append(f"{int(count)} checkpoint_blobs rows become unreferenced")
        total += int(blob_bytes)
    return total


async def _delete_one_batch(
    engine: AsyncEngine,
    tables: _Tables,
    cte: str,
    params: dict,
    batch_size: int,
    *,
    has_writes: bool,
    has_blobs: bool,
    with_runs: bool,
    report: RetentionReport,
) -> int:
    """Delete the oldest ``batch_size`` doomed checkpoints in one transaction."""
    async with engine.begin() as conn:
        rows = (await conn.execute(text(cte + "SELECT thread_id, checkpoint_ns, checkpoint_id FROM doomed ORDER BY checkpoint_id LIMIT :limit"), {**params, "limit": batch_size})).all()
        if not rows:
            return 0
        if with_runs:
            # The plan was computed a moment ago; a run may have started since.
            # Re-check inside this transaction and leave those threads alone.
            threads = sorted({r[0] for r in rows})
            busy: set[str] = set()
            for thread_id in threads:
                hit = (
                    await conn.execute(
                        text(f"SELECT 1 FROM runs WHERE thread_id = :thread_id AND status IN ({', '.join(f"'{s}'" for s in ACTIVE_RUN_STATUSES)}) LIMIT 1"),
                        {"thread_id": thread_id},
                    )
                ).first()
                if hit:
                    busy.add(thread_id)
            if busy:
                rows = [r for r in rows if r[0] not in busy]
                report.notes.append(f"skipped {len(busy)} thread(s) whose run started mid-sweep")
                if not rows:
                    return 0

        groups: dict[tuple[str, str], list[str]] = {}
        for thread_id, checkpoint_ns, checkpoint_id in rows:
            groups.setdefault((thread_id, checkpoint_ns), []).append(checkpoint_id)

        # One statement per (thread, ns) with an expanding IN list, rather than
        # executemany: rowcount is exact on every driver this way.
        where = "thread_id = :thread_id AND checkpoint_ns = :checkpoint_ns AND checkpoint_id IN :ids"
        for (thread_id, checkpoint_ns), ids in sorted(groups.items()):
            bind = {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "ids": ids}
            # Children first, then the checkpoints, then the blobs no survivor references.
            if has_writes:
                result = await conn.execute(text(f"DELETE FROM {tables.writes} WHERE {where}").bindparams(bindparam("ids", expanding=True)), bind)
                report.deleted_writes += max(result.rowcount or 0, 0)
            result = await conn.execute(text(f"DELETE FROM {tables.checkpoints} WHERE {where}").bindparams(bindparam("ids", expanding=True)), bind)
            report.deleted_checkpoints += max(result.rowcount or 0, 0)
            if has_blobs:
                result = await conn.execute(text(_PG_BLOB_GC_ONE), {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns})
                report.deleted_blobs += max(result.rowcount or 0, 0)
        return len(rows)
