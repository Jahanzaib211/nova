"""``maintenance.checkpoints.prune``: the checkpoint retention sweep as a job.

Admin-only to enqueue or schedule (it is not in ``USER_SCHEDULABLE_JOB_TYPES``);
schedule it with a cron such as ``0 4 * * *``. Reads ``checkpoint_retention``
per run, so config edits apply to the next sweep without a worker restart.

The job never deletes unless ``checkpoint_retention.enabled`` is true **and**
``checkpoint_retention.dry_run`` is false. A payload may force a dry run
(``{"dry_run": true}``) but can never turn one off.
"""

from __future__ import annotations

import logging

from nova.config.app_config import get_app_config
from nova.jobs.context import JobContext
from nova.jobs.registry import JobRegistry
from nova.persistence.checkpoint_retention import RetentionPolicy, prune_checkpoints
from nova.persistence.engine import get_engine

logger = logging.getLogger(__name__)

CHECKPOINT_PRUNE_JOB = "maintenance.checkpoints.prune"


async def checkpoint_prune(ctx: JobContext) -> dict:
    cfg = get_app_config().checkpoint_retention
    if not cfg.enabled:
        return {"skipped": "checkpoint_retention.enabled is false"}
    engine = get_engine()
    if engine is None:
        return {"skipped": "database.backend is memory; no checkpoint tables to prune"}
    dry_run = bool(cfg.dry_run or (ctx.payload or {}).get("dry_run"))
    await ctx.heartbeat()
    report = await prune_checkpoints(engine, RetentionPolicy.from_config(cfg), dry_run=dry_run)
    return report.as_dict()


def register(registry: JobRegistry) -> None:
    registry.register(CHECKPOINT_PRUNE_JOB, checkpoint_prune)
