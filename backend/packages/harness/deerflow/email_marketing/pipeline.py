"""The campaign pipeline, as job handlers.

``em.campaign.start``  snapshot recipients (list members, subscribed, minus
                       suppressions) into ``em_sends`` and enqueue the first
                       batch.
``em.campaign.batch``  claim up to ``batch_size`` queued sends, render each
                       for its contact, deliver through the SMTP relay in a
                       worker thread, record events, suppress hard bounces,
                       requeue throttled/deferred sends and enqueue the next
                       batch (spaced by the throttle) until nothing is queued.

Pause/cancel are read from the campaign row at the top of every batch, so a
paused campaign stops within one batch and resumes by enqueueing a batch.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from deerflow.config.email_config import EmailConfig
from deerflow.config.email_marketing_config import EmailMarketingConfig
from deerflow.email_marketing.smtp import DomainThrottle, OutboundMessage, SendResult, SmtpSender
from deerflow.email_marketing.templates import TemplateError, render_template
from deerflow.email_marketing.tracking import TrackingLinks
from deerflow.jobs.context import JobContext
from deerflow.jobs.queue import JobQueue
from deerflow.persistence.email_marketing.sql import EmailMarketingRepository

logger = logging.getLogger(__name__)

JOB_START = "em.campaign.start"
JOB_BATCH = "em.campaign.batch"
JOB_BOUNCE_POLL = "em.bounce.poll"
JOB_IMPORT = "em.contacts.import"
QUEUE = "email"


@dataclass
class PipelineDeps:
    em: EmailMarketingRepository
    queue: JobQueue
    config: EmailMarketingConfig
    email: EmailConfig
    sender_factory: Callable[..., Any] | None = None


class CampaignPipeline:
    def __init__(self, deps: PipelineDeps) -> None:
        self.deps = deps

    # ------------------------------------------------------------- helpers

    def _sender(self, campaign: dict[str, Any]) -> Any:
        e, cfg = self.deps.email, self.deps.config
        kwargs = dict(
            host=e.host,
            port=e.port,
            use_tls=e.use_tls,
            use_ssl=e.use_ssl,
            username=e.username,
            password=e.password,
            from_email=campaign.get("from_email") or e.from_email,
            from_name=campaign.get("from_name") or e.from_name,
            reply_to=campaign.get("reply_to"),
            bounce_domain=cfg.bounce_domain or (campaign.get("from_email") or e.from_email).rsplit("@", 1)[-1],
            throttle=DomainThrottle(cfg.per_domain_per_minute),
        )
        if self.deps.sender_factory is not None:
            return self.deps.sender_factory(**kwargs)
        return SmtpSender(**kwargs)

    async def _enqueue_batch(self, owner: str, campaign_id: str, *, delay: timedelta | None = None) -> str:
        return await self.deps.queue.enqueue(
            JOB_BATCH,
            {"campaign_id": campaign_id},
            queue=QUEUE,
            owner_user_id=owner,
            run_after=(datetime.now(UTC) + delay) if delay else None,
            # No dedupe key: the running batch would own it and the follow-up
            # would silently collapse into it. Concurrent batches are safe —
            # claim_queued_sends hands each one distinct sends.
        )

    async def _refresh_stats(self, owner: str, campaign_id: str, status: str | None = None) -> dict[str, Any]:
        stats = await self.deps.em.campaign_stats(owner, campaign_id) or {}
        await self.deps.em.update_campaign_status(owner, campaign_id, status or (await self.deps.em.get_campaign(owner, campaign_id))["status"], stats=stats)
        return stats

    # ------------------------------------------------------------ handlers

    async def start(self, ctx: JobContext) -> dict[str, Any]:
        campaign_id = str(ctx.payload["campaign_id"])
        campaign = await self.deps.em.get_campaign_any_owner(campaign_id)
        if campaign is None:
            raise RuntimeError(f"campaign {campaign_id} not found")
        owner = campaign["owner_user_id"]
        ok, why = self.deps.config.send_ready()
        if not ok or not self.deps.email.enabled or not self.deps.email.host:
            reason = why or "email (SMTP) is not enabled"
            await self.deps.em.update_campaign_status(owner, campaign_id, "failed", error=reason)
            raise RuntimeError(reason)
        if campaign["status"] in ("completed", "cancelled"):
            return {"skipped": campaign["status"]}
        template = await self.deps.em.get_template(owner, campaign["template_id"])
        if template is None:
            await self.deps.em.update_campaign_status(owner, campaign_id, "failed", error="template missing")
            raise RuntimeError("template missing")
        recipients = await self.deps.em.recipients_for_list(owner, campaign["list_id"])
        sends = await self.deps.em.create_sends(owner, campaign_id, recipients)
        for s in sends:
            await self.deps.em.record_event(owner, type="queued", campaign_id=campaign_id, send_id=s["id"], contact_id=s["contact_id"])
        await ctx.progress(10, f"{len(sends)} recipients queued")
        if not sends and not await self.deps.em.send_counts(campaign_id):
            await self._refresh_stats(owner, campaign_id, "completed")
            return {"recipients": 0, "batches": 0}
        await self.deps.em.update_campaign_status(owner, campaign_id, "sending", job_id=ctx.job_id)
        await self._refresh_stats(owner, campaign_id)
        await self._enqueue_batch(owner, campaign_id)
        return {"recipients": len(sends), "batches": 1}

    async def batch(self, ctx: JobContext) -> dict[str, Any]:
        campaign_id = str(ctx.payload["campaign_id"])
        campaign = await self.deps.em.get_campaign_any_owner(campaign_id)
        if campaign is None:
            raise RuntimeError(f"campaign {campaign_id} not found")
        owner = campaign["owner_user_id"]
        if campaign["status"] != "sending":
            return {"skipped": campaign["status"]}
        template = await self.deps.em.get_template(owner, campaign["template_id"])
        if template is None:
            await self.deps.em.update_campaign_status(owner, campaign_id, "failed", error="template missing")
            raise RuntimeError("template missing")
        cfg = self.deps.config
        await ctx.heartbeat()
        # Anything a killed batch left in `sending` goes back first.
        await self.deps.em.requeue_sending(campaign_id)
        claimed = await self.deps.em.claim_queued_sends(campaign_id, limit=cfg.batch_size)
        if not claimed:
            await self._refresh_stats(owner, campaign_id, "completed")
            return {"sent": 0, "failed": 0, "requeued": 0, "done": True}

        messages: list[OutboundMessage] = []
        render_failures: list[tuple[dict[str, Any], str]] = []
        for send in claimed:
            contact = await self.deps.em.get_contact(owner, send["contact_id"]) or {"email": send["email"]}
            links = TrackingLinks(base_url=cfg.public_base_url, secret=cfg.tracking_secret, send_id=send["id"])
            try:
                rendered = render_template(subject=template["subject"], html=template["html"], text=template["text"], contact=contact, links=links)
            except TemplateError as exc:
                render_failures.append((send, str(exc)))
                continue
            bounce_domain = cfg.bounce_domain or (campaign.get("from_email") or self.deps.email.from_email).rsplit("@", 1)[-1]
            messages.append(
                OutboundMessage(
                    send_id=send["id"],
                    to=send["email"],
                    subject=rendered.subject,
                    html=rendered.html,
                    text=rendered.text,
                    unsubscribe_url=links.unsubscribe_url(),
                    unsubscribe_mailto=links.unsubscribe_mailto(bounce_domain),
                )
            )
        for send, err in render_failures:
            await self.deps.em.mark_send(send["id"], status="failed", error=f"template: {err}")
            await self.deps.em.record_event(owner, type="failed", campaign_id=campaign_id, send_id=send["id"], contact_id=send["contact_id"], payload={"error": err[:500]})

        sender = self._sender(campaign)
        results: list[SendResult] = await asyncio.to_thread(sender.send_batch, messages) if messages else []
        by_id = {s["id"]: s for s in claimed}
        sent = failed = requeued = 0
        for r in results:
            send = by_id[r.send_id]
            await ctx.heartbeat()
            if r.status == "sent":
                sent += 1
                await self.deps.em.mark_send(r.send_id, status="sent", message_id=r.message_id)
                await self.deps.em.record_event(owner, type="sent", campaign_id=campaign_id, send_id=r.send_id, contact_id=send["contact_id"], payload={"message_id": r.message_id})
            elif r.status in ("throttled", "deferred"):
                requeued += 1
                await self.deps.em.mark_send(r.send_id, status="queued", error=r.error)
                if r.status == "deferred":
                    await self.deps.em.record_event(owner, type="deferred", campaign_id=campaign_id, send_id=r.send_id, contact_id=send["contact_id"], payload={"error": r.error or ""})
            else:  # bounced_hard | bounced_soft
                failed += 1
                await self.deps.em.mark_send(r.send_id, status="failed", error=r.error)
                await self.deps.em.record_event(owner, type=r.status, campaign_id=campaign_id, send_id=r.send_id, contact_id=send["contact_id"], payload={"error": r.error or ""})
                if r.status == "bounced_hard":
                    await self.deps.em.suppress(owner, send["email"], reason="hard_bounce", detail=r.error)
                    await self.deps.em.record_event(owner, type="suppressed", campaign_id=campaign_id, send_id=r.send_id, contact_id=send["contact_id"], payload={"reason": "hard_bounce"})
        failed += len(render_failures)

        counts = await self.deps.em.send_counts(campaign_id)
        remaining = counts.get("queued", 0)
        await ctx.progress(int(100 * (1 - remaining / max(1, sum(counts.values())))), f"{sent} sent, {failed} failed, {remaining} left")
        if remaining:
            per_minute = campaign.get("throttle_per_minute") or cfg.default_throttle_per_minute
            spacing = timedelta(seconds=60.0 * min(len(messages), cfg.batch_size) / max(1, per_minute))
            if requeued and sent == 0:
                spacing = max(spacing, timedelta(seconds=30))
            await self._refresh_stats(owner, campaign_id)
            await self._enqueue_batch(owner, campaign_id, delay=spacing)
            return {"sent": sent, "failed": failed, "requeued": requeued, "remaining": remaining}
        await self._refresh_stats(owner, campaign_id, "completed")
        return {"sent": sent, "failed": failed, "requeued": requeued, "done": True}
