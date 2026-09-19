"""Email-marketing jobs: campaign start/batch, IMAP bounce poll, CSV import.

Handlers build their dependencies from the freshest config on each run (the
section hot-reloads) and share the worker's session factory.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from deerflow.config.app_config import get_app_config
from deerflow.email_marketing.contacts_import import parse_contacts_csv
from deerflow.email_marketing.imap import BouncePoller
from deerflow.email_marketing.pipeline import JOB_BATCH, JOB_BOUNCE_POLL, JOB_IMPORT, JOB_START, CampaignPipeline, PipelineDeps
from deerflow.jobs.context import JobContext
from deerflow.jobs.queue import JobQueue
from deerflow.jobs.registry import JobRegistry
from deerflow.persistence.email_marketing.sql import EmailMarketingRepository
from deerflow.persistence.engine import get_session_factory

logger = logging.getLogger(__name__)

JOB_PRUNE = "em.events.prune"


def _repo() -> EmailMarketingRepository:
    sf = get_session_factory()
    if sf is None:
        raise RuntimeError("database engine not initialised")
    return EmailMarketingRepository(sf)


def _pipeline(ctx: JobContext) -> CampaignPipeline:
    cfg = get_app_config()
    return CampaignPipeline(PipelineDeps(em=_repo(), queue=JobQueue(ctx._repo), config=cfg.email_marketing, email=cfg.email))


async def campaign_start(ctx: JobContext) -> dict:
    return await _pipeline(ctx).start(ctx)


async def campaign_batch(ctx: JobContext) -> dict:
    return await _pipeline(ctx).batch(ctx)


async def bounce_poll(ctx: JobContext) -> dict:
    cfg = get_app_config().email_marketing
    if not cfg.enabled or not cfg.bounce_mailbox.host or not cfg.bounce_mailbox.username:
        return {"skipped": "bounce mailbox not configured"}
    owner = ctx.owner_user_id or str(ctx.payload.get("owner_user_id") or "")
    if not owner:
        return {"skipped": "no owner"}
    return await BouncePoller(_repo(), cfg.bounce_mailbox).poll(owner)


async def contacts_import(ctx: JobContext) -> dict:
    """Payload: ``{list_id?, mapping, csv_text}`` (text is small enough to
    ride in the payload; the UI caps uploads at a few MB)."""
    owner = ctx.owner_user_id or ""
    if not owner:
        raise RuntimeError("import needs an owner")
    rows, report = parse_contacts_csv(str(ctx.payload.get("csv_text", "")), dict(ctx.payload.get("mapping") or {}))
    repo = _repo()
    ids: list[str] = []
    for i, row in enumerate(rows, 1):
        contact = await repo.upsert_contact(owner, email=row["email"], first_name=row["first_name"], last_name=row["last_name"], attributes=row["attributes"])
        ids.append(contact["id"])
        if i % 50 == 0:
            await ctx.heartbeat()
            await ctx.progress(int(90 * i / max(1, len(rows))), f"{i}/{len(rows)} contacts")
    added = 0
    list_id = ctx.payload.get("list_id")
    if list_id and ids:
        added = await repo.add_members(owner, str(list_id), ids)
    await ctx.progress(100, f"{len(ids)} contacts, {added} added to list")
    return {**report, "imported": len(ids), "added_to_list": added}


async def events_prune(ctx: JobContext) -> dict:
    cfg = get_app_config().email_marketing
    cutoff = datetime.now(UTC) - timedelta(days=cfg.events_retention_days)
    return {"pruned": await _repo().prune_events(cutoff)}


def register(registry: JobRegistry) -> None:
    registry.register(JOB_START, campaign_start)
    registry.register(JOB_BATCH, campaign_batch)
    registry.register(JOB_BOUNCE_POLL, bounce_poll)
    registry.register(JOB_IMPORT, contacts_import)
    registry.register(JOB_PRUNE, events_prune)
