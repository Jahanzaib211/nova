"""Bound the run-events table.

After the checkpoint tables were vacuumed (6,084 MB -> 439 MB, see
``scripts/pg-autovacuum-tuning.sql``), ``run_events`` was the largest thing left
and the only one that vacuuming cannot help: 366 MB of which 335 MB is live
content. It grows with every run and nothing ever removed a row.

This is a plain periodic task rather than a cron entry or an external script so
that a deployment gets the bound by configuration alone, with no second thing to
install and forget. It is **off unless configured** -- deleting a user's history
as a side effect of an upgrade would be a worse bug than the growth.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging

logger = logging.getLogger(__name__)

#: How often to sweep. Retention is measured in days, so hourly is already far
#: more often than it needs to be; it is cheap because the delete is indexed on
#: created_at and finds nothing on all but the first pass after a cutoff moves.
PRUNE_INTERVAL_SECONDS = 3600.0


async def prune_once(store, retention_days: int) -> int:
    """Delete events older than the retention window. Returns rows removed."""
    if retention_days <= 0:
        return 0
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=retention_days)
    deleted = await store.delete_older_than(cutoff)
    if deleted:
        logger.info("run-events retention: removed %d events older than %s", deleted, cutoff.isoformat())
    return deleted


async def _loop(store, retention_days: int) -> None:
    while True:
        try:
            await prune_once(store, retention_days)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Retention is maintenance. A failure here must never take down the
            # gateway or stop future sweeps -- the next tick tries again.
            logger.warning("run-events retention sweep failed", exc_info=True)
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)


def start_run_event_pruner(*, store, retention_days: int) -> asyncio.Task | None:
    """Start the sweep, or return None when retention is disabled."""
    if retention_days <= 0:
        logger.debug("run-events retention disabled (retention_days=%s)", retention_days)
        return None
    task = asyncio.create_task(_loop(store, retention_days), name="run-events-retention")
    logger.info("run-events retention active: %d days", retention_days)
    return task


async def stop_run_event_pruner(task: asyncio.Task | None) -> None:
    """Cancel the sweep on lifespan exit; safe to call with None."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
