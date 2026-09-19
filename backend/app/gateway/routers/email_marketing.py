"""``/api/em`` — email marketing, owner-scoped.

Lists, contacts (+ CSV import as a job), templates (+ sandboxed preview),
campaigns (create → preflight → send-now/schedule → pause/resume/cancel),
stats, events and suppressions. Sending itself is the job runner's work
(``em.campaign.start`` → ``em.campaign.batch``); this router only enqueues.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.authz import AuthContext, require_auth
from app.gateway.deps import get_config, get_em_repo, get_jobs_repo
from deerflow.email_marketing.contacts_import import guess_mapping, parse_contacts_csv
from deerflow.email_marketing.events import CONTACT_STATUSES, SUPPRESSION_REASONS
from deerflow.email_marketing.pipeline import JOB_BATCH, JOB_IMPORT, JOB_START, QUEUE
from deerflow.email_marketing.templates import TemplateError, render_template, validate_template
from deerflow.email_marketing.tracking import TrackingLinks
from deerflow.jobs.queue import JobQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/em", tags=["email-marketing"])

MAX_CSV_BYTES = 5 * 1024 * 1024


# ---------------------------------------------------------------- helpers


def _user_id(request: Request) -> str:
    auth: AuthContext = request.state.auth
    if auth.user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(auth.user.id)


def _enabled() -> None:
    if not get_config().email_marketing.enabled:
        raise HTTPException(status_code=404, detail="Email marketing is disabled (email_marketing.enabled)")


def _found(value: Any, what: str) -> Any:
    if value is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return value


# ----------------------------------------------------------------- models


class ListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None


class ListUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None


class MembersRequest(BaseModel):
    contact_ids: list[str] = Field(min_length=1, max_length=5000)


class ContactCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    first_name: str | None = None
    last_name: str | None = None
    status: str | None = None
    attributes: dict[str, Any] | None = None


class ImportRequest(BaseModel):
    csv_text: str = Field(min_length=1)
    mapping: dict[str, str]
    list_id: str | None = None


class MappingRequest(BaseModel):
    csv_text: str = Field(min_length=1)


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    subject: str = Field(max_length=998)
    html: str
    text: str | None = None


class TemplateUpdate(BaseModel):
    name: str | None = None
    subject: str | None = None
    html: str | None = None
    text: str | None = None


class PreviewRequest(BaseModel):
    contact_id: str | None = None


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    list_id: str
    template_id: str
    from_email: str = Field(min_length=3, max_length=320)
    from_name: str = Field(default="", max_length=128)
    reply_to: str | None = None
    scheduled_at: datetime | None = None
    throttle_per_minute: int | None = Field(default=None, ge=1, le=10000)


class CampaignUpdate(BaseModel):
    name: str | None = None
    list_id: str | None = None
    template_id: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    scheduled_at: datetime | None = None
    throttle_per_minute: int | None = Field(default=None, ge=1, le=10000)


class SuppressionCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    reason: str = "manual"
    detail: str | None = None


class TestSendRequest(BaseModel):
    to: str = Field(min_length=3, max_length=320)


# ------------------------------------------------------------------ lists


@router.get("/lists")
@require_auth
async def list_lists(request: Request) -> dict[str, Any]:
    _enabled()
    return {"lists": await get_em_repo(request).list_lists(_user_id(request))}


@router.post("/lists", status_code=201)
@require_auth
async def create_list(body: ListCreate, request: Request) -> dict[str, Any]:
    _enabled()
    return await get_em_repo(request).create_list(_user_id(request), name=body.name, description=body.description)


@router.get("/lists/{list_id}")
@require_auth
async def get_list(list_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    return _found(await get_em_repo(request).get_list(_user_id(request), list_id), "List")


@router.patch("/lists/{list_id}")
@require_auth
async def update_list(list_id: str, body: ListUpdate, request: Request) -> dict[str, Any]:
    _enabled()
    return _found(await get_em_repo(request).update_list(_user_id(request), list_id, **body.model_dump()), "List")


@router.delete("/lists/{list_id}")
@require_auth
async def delete_list(list_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    if not await get_em_repo(request).delete_list(_user_id(request), list_id):
        raise HTTPException(status_code=404, detail="List not found")
    return {"ok": True}


@router.get("/lists/{list_id}/members")
@require_auth
async def list_members(list_id: str, request: Request, limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    _found(await repo.get_list(_user_id(request), list_id), "List")
    return {"contacts": await repo.list_members(_user_id(request), list_id, limit=limit, offset=offset)}


@router.post("/lists/{list_id}/members")
@require_auth
async def add_members(list_id: str, body: MembersRequest, request: Request) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    _found(await repo.get_list(_user_id(request), list_id), "List")
    return {"added": await repo.add_members(_user_id(request), list_id, body.contact_ids)}


@router.delete("/lists/{list_id}/members/{contact_id}")
@require_auth
async def remove_member(list_id: str, contact_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    if not await get_em_repo(request).remove_member(_user_id(request), list_id, contact_id):
        raise HTTPException(status_code=404, detail="Membership not found")
    return {"ok": True}


# --------------------------------------------------------------- contacts


@router.get("/contacts")
@require_auth
async def list_contacts(
    request: Request,
    q: str | None = Query(default=None, max_length=320),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _enabled()
    if status is not None and status not in CONTACT_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(CONTACT_STATUSES)}")
    return await get_em_repo(request).list_contacts(_user_id(request), query=q, status=status, limit=limit, offset=offset)


@router.post("/contacts", status_code=201)
@require_auth
async def create_contact(body: ContactCreate, request: Request) -> dict[str, Any]:
    _enabled()
    if body.status is not None and body.status not in CONTACT_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(CONTACT_STATUSES)}")
    from deerflow.email_marketing.contacts_import import is_valid_email

    if not is_valid_email(body.email.strip()):
        raise HTTPException(status_code=422, detail="Invalid email address")
    return await get_em_repo(request).upsert_contact(_user_id(request), email=body.email, first_name=body.first_name, last_name=body.last_name, status=body.status, attributes=body.attributes)


@router.post("/contacts/import/mapping")
@require_auth
async def import_mapping(body: MappingRequest, request: Request) -> dict[str, Any]:
    """Guess field → column for a CSV's header line."""
    _enabled()
    header = body.csv_text.splitlines()[0] if body.csv_text else ""
    import csv
    import io

    headers = next(csv.reader(io.StringIO(header)), [])
    return {"headers": headers, "mapping": guess_mapping(headers)}


@router.post("/contacts/import", status_code=202)
@require_auth
async def import_contacts(body: ImportRequest, request: Request) -> dict[str, Any]:
    _enabled()
    if len(body.csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="CSV larger than 5 MB; split the file")
    if "email" not in body.mapping:
        raise HTTPException(status_code=422, detail="mapping.email is required")
    try:
        _, preview = parse_contacts_csv(body.csv_text, body.mapping)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"CSV could not be parsed: {exc}") from None
    owner = _user_id(request)
    if body.list_id:
        _found(await get_em_repo(request).get_list(owner, body.list_id), "List")
    job_id = await JobQueue(get_jobs_repo(request)).enqueue(JOB_IMPORT, {"csv_text": body.csv_text, "mapping": body.mapping, "list_id": body.list_id}, queue=QUEUE, owner_user_id=owner)
    return {"job_id": job_id, "preview": preview}


@router.get("/contacts/{contact_id}")
@require_auth
async def get_contact(contact_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    return _found(await get_em_repo(request).get_contact(_user_id(request), contact_id), "Contact")


@router.delete("/contacts/{contact_id}")
@require_auth
async def delete_contact(contact_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    if not await get_em_repo(request).delete_contact(_user_id(request), contact_id):
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"ok": True}


# -------------------------------------------------------------- templates


@router.get("/templates")
@require_auth
async def list_templates(request: Request) -> dict[str, Any]:
    _enabled()
    return {"templates": await get_em_repo(request).list_templates(_user_id(request))}


def _check_template(subject: str | None, html: str | None, text: str | None) -> None:
    for name, source in (("subject", subject), ("html", html), ("text", text)):
        if source:
            err = validate_template(source)
            if err:
                raise HTTPException(status_code=422, detail=f"{name}: {err}")


@router.post("/templates", status_code=201)
@require_auth
async def create_template(body: TemplateCreate, request: Request) -> dict[str, Any]:
    _enabled()
    _check_template(body.subject, body.html, body.text)
    return await get_em_repo(request).create_template(_user_id(request), name=body.name, subject=body.subject, html=body.html, text=body.text)


@router.get("/templates/{template_id}")
@require_auth
async def get_template(template_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    return _found(await get_em_repo(request).get_template(_user_id(request), template_id), "Template")


@router.patch("/templates/{template_id}")
@require_auth
async def update_template(template_id: str, body: TemplateUpdate, request: Request) -> dict[str, Any]:
    _enabled()
    _check_template(body.subject, body.html, body.text)
    return _found(await get_em_repo(request).update_template(_user_id(request), template_id, **body.model_dump()), "Template")


@router.delete("/templates/{template_id}")
@require_auth
async def delete_template(template_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    if not await get_em_repo(request).delete_template(_user_id(request), template_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True}


@router.post("/templates/{template_id}/preview")
@require_auth
async def preview_template(template_id: str, body: PreviewRequest, request: Request) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    owner = _user_id(request)
    tpl = _found(await repo.get_template(owner, template_id), "Template")
    contact = _found(await repo.get_contact(owner, body.contact_id), "Contact") if body.contact_id else {"email": "preview@example.com", "first_name": "Ada", "last_name": "Lovelace", "attributes": {}}
    cfg = get_config().email_marketing
    links = TrackingLinks(base_url=cfg.public_base_url or "https://example.invalid", secret=cfg.tracking_secret or "preview", send_id="preview")
    try:
        out = render_template(subject=tpl["subject"], html=tpl["html"], text=tpl["text"], contact=contact, links=links)
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"subject": out.subject, "html": out.html, "text": out.text}


# -------------------------------------------------------------- campaigns


@router.get("/campaigns")
@require_auth
async def list_campaigns(request: Request) -> dict[str, Any]:
    _enabled()
    return {"campaigns": await get_em_repo(request).list_campaigns(_user_id(request))}


@router.post("/campaigns", status_code=201)
@require_auth
async def create_campaign(body: CampaignCreate, request: Request) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    owner = _user_id(request)
    _found(await repo.get_list(owner, body.list_id), "List")
    _found(await repo.get_template(owner, body.template_id), "Template")
    return await repo.create_campaign(owner, **body.model_dump())


@router.get("/campaigns/{campaign_id}")
@require_auth
async def get_campaign(campaign_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    return _found(await get_em_repo(request).get_campaign(_user_id(request), campaign_id), "Campaign")


@router.patch("/campaigns/{campaign_id}")
@require_auth
async def update_campaign(campaign_id: str, body: CampaignUpdate, request: Request) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    owner = _user_id(request)
    camp = _found(await repo.get_campaign(owner, campaign_id), "Campaign")
    if camp["status"] not in ("draft", "scheduled"):
        raise HTTPException(status_code=409, detail=f"Campaign is {camp['status']}; only drafts can be edited")
    return _found(await repo.update_campaign(owner, campaign_id, **body.model_dump(exclude_unset=True)), "Campaign")


@router.delete("/campaigns/{campaign_id}")
@require_auth
async def delete_campaign(campaign_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    if not await get_em_repo(request).delete_campaign(_user_id(request), campaign_id):
        raise HTTPException(status_code=404, detail="Campaign not found or still sending")
    return {"ok": True}


async def _preflight(request: Request, owner: str, camp: dict[str, Any]) -> dict[str, Any]:
    repo = get_em_repo(request)
    cfg = get_config()
    problems: list[str] = []
    ok, why = cfg.email_marketing.send_ready()
    if not ok:
        problems.append(why)
    if not cfg.email.enabled or not cfg.email.host:
        problems.append("email (SMTP relay) is not enabled in config.yaml")
    tpl = await repo.get_template(owner, camp["template_id"])
    if tpl is None:
        problems.append("template missing")
    else:
        for name, source in (("subject", tpl["subject"]), ("html", tpl["html"])):
            err = validate_template(source) if source else None
            if err:
                problems.append(f"template {name}: {err}")
    lst = await repo.get_list(owner, camp["list_id"])
    recipients = await repo.recipients_for_list(owner, camp["list_id"]) if lst else []
    suppressed = max(0, (lst["member_count"] if lst else 0) - len(recipients))
    if lst is None:
        problems.append("list missing")
    elif not recipients:
        problems.append("no recipients (every member is unsubscribed or suppressed)")
    if not camp["from_email"]:
        problems.append("from_email is empty")
    return {"ok": not problems, "problems": problems, "recipients": len(recipients), "suppressed": suppressed, "list": lst["name"] if lst else None, "template": tpl["name"] if tpl else None}


@router.get("/campaigns/{campaign_id}/preflight")
@require_auth
async def preflight(campaign_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    owner = _user_id(request)
    camp = _found(await get_em_repo(request).get_campaign(owner, campaign_id), "Campaign")
    return await _preflight(request, owner, camp)


@router.post("/campaigns/{campaign_id}/send-now")
@require_auth
async def send_now(campaign_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    owner = _user_id(request)
    camp = _found(await repo.get_campaign(owner, campaign_id), "Campaign")
    if camp["status"] not in ("draft", "scheduled"):
        raise HTTPException(status_code=409, detail=f"Campaign is {camp['status']}")
    pre = await _preflight(request, owner, camp)
    if not pre["ok"]:
        raise HTTPException(status_code=409, detail="; ".join(pre["problems"]))
    job_id = await JobQueue(get_jobs_repo(request)).enqueue(JOB_START, {"campaign_id": campaign_id}, queue=QUEUE, owner_user_id=owner, dedupe_key=f"em-start:{campaign_id}")
    return await repo.update_campaign_status(owner, campaign_id, "sending", job_id=job_id)


@router.post("/campaigns/{campaign_id}/schedule")
@require_auth
async def schedule(campaign_id: str, body: CampaignUpdate, request: Request) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    owner = _user_id(request)
    camp = _found(await repo.get_campaign(owner, campaign_id), "Campaign")
    if camp["status"] not in ("draft", "scheduled"):
        raise HTTPException(status_code=409, detail=f"Campaign is {camp['status']}")
    if body.scheduled_at is None:
        raise HTTPException(status_code=422, detail="scheduled_at is required")
    pre = await _preflight(request, owner, camp)
    if not pre["ok"]:
        raise HTTPException(status_code=409, detail="; ".join(pre["problems"]))
    job_id = await JobQueue(get_jobs_repo(request)).enqueue(JOB_START, {"campaign_id": campaign_id}, queue=QUEUE, owner_user_id=owner, run_after=body.scheduled_at, dedupe_key=f"em-start:{campaign_id}")
    await repo.update_campaign(owner, campaign_id, scheduled_at=body.scheduled_at)
    return await repo.update_campaign_status(owner, campaign_id, "scheduled", job_id=job_id)


@router.post("/campaigns/{campaign_id}/pause")
@require_auth
async def pause(campaign_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    owner = _user_id(request)
    camp = _found(await repo.get_campaign(owner, campaign_id), "Campaign")
    if camp["status"] != "sending":
        raise HTTPException(status_code=409, detail=f"Campaign is {camp['status']}")
    return await repo.update_campaign_status(owner, campaign_id, "paused")


@router.post("/campaigns/{campaign_id}/resume")
@require_auth
async def resume(campaign_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    owner = _user_id(request)
    camp = _found(await repo.get_campaign(owner, campaign_id), "Campaign")
    if camp["status"] != "paused":
        raise HTTPException(status_code=409, detail=f"Campaign is {camp['status']}")
    await repo.update_campaign_status(owner, campaign_id, "sending")
    job_id = await JobQueue(get_jobs_repo(request)).enqueue(JOB_BATCH, {"campaign_id": campaign_id}, queue=QUEUE, owner_user_id=owner)
    return await repo.update_campaign_status(owner, campaign_id, "sending", job_id=job_id)


@router.post("/campaigns/{campaign_id}/cancel")
@require_auth
async def cancel(campaign_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    owner = _user_id(request)
    camp = _found(await repo.get_campaign(owner, campaign_id), "Campaign")
    if camp["status"] in ("completed", "cancelled", "failed"):
        raise HTTPException(status_code=409, detail=f"Campaign is {camp['status']}")
    if camp.get("job_id"):
        try:
            await get_jobs_repo(request).request_cancel(camp["job_id"])
        except Exception:  # the job may already be terminal
            logger.debug("cancel: job %s not cancellable", camp["job_id"], exc_info=True)
    stats = await repo.campaign_stats(owner, campaign_id) or {}
    return await repo.update_campaign_status(owner, campaign_id, "cancelled", stats=stats)


@router.post("/campaigns/{campaign_id}/test-send")
@require_auth
async def test_send(campaign_id: str, body: TestSendRequest, request: Request) -> dict[str, Any]:
    """Send the rendered campaign to one address right now, outside the
    campaign's own sends (no tracking rows, no stats)."""
    _enabled()
    import asyncio

    from deerflow.email_marketing.smtp import OutboundMessage, SmtpSender

    repo = get_em_repo(request)
    owner = _user_id(request)
    camp = _found(await repo.get_campaign(owner, campaign_id), "Campaign")
    tpl = _found(await repo.get_template(owner, camp["template_id"]), "Template")
    cfg = get_config()
    ok, why = cfg.email_marketing.send_ready()
    if not ok or not cfg.email.enabled:
        raise HTTPException(status_code=409, detail=why or "email is not enabled")
    links = TrackingLinks(base_url=cfg.email_marketing.public_base_url, secret=cfg.email_marketing.tracking_secret, send_id="test")
    try:
        out = render_template(subject=f"[TEST] {tpl['subject']}", html=tpl["html"], text=tpl["text"], contact={"email": body.to, "first_name": "Test", "last_name": "Recipient", "attributes": {}}, links=links)
    except TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    e = cfg.email
    sender = SmtpSender(
        host=e.host,
        port=e.port,
        use_tls=e.use_tls,
        use_ssl=e.use_ssl,
        username=e.username,
        password=e.password,
        from_email=camp["from_email"] or e.from_email,
        from_name=camp["from_name"] or e.from_name,
        reply_to=camp.get("reply_to"),
        bounce_domain=cfg.email_marketing.bounce_domain,
    )
    results = await asyncio.to_thread(sender.send_batch, [OutboundMessage(send_id="test", to=body.to, subject=out.subject, html=out.html, text=out.text)])
    r = results[0]
    return {"status": r.status, "message_id": r.message_id, "error": r.error}


@router.get("/campaigns/{campaign_id}/stats")
@require_auth
async def stats(campaign_id: str, request: Request) -> dict[str, Any]:
    _enabled()
    return _found(await get_em_repo(request).campaign_stats(_user_id(request), campaign_id), "Campaign")


@router.get("/campaigns/{campaign_id}/events")
@require_auth
async def campaign_events(campaign_id: str, request: Request, type: str | None = Query(default=None), limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    owner = _user_id(request)
    _found(await repo.get_campaign(owner, campaign_id), "Campaign")
    return {"events": await repo.list_events(owner, campaign_id=campaign_id, type=type, limit=limit, offset=offset)}


@router.get("/campaigns/{campaign_id}/sends")
@require_auth
async def campaign_sends(campaign_id: str, request: Request, status: str | None = Query(default=None), limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    _enabled()
    repo = get_em_repo(request)
    owner = _user_id(request)
    _found(await repo.get_campaign(owner, campaign_id), "Campaign")
    return {"sends": await repo.list_sends(owner, campaign_id, status=status, limit=limit, offset=offset)}


# ------------------------------------------------------------ suppressions


@router.get("/suppressions")
@require_auth
async def list_suppressions(request: Request, limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    _enabled()
    return {"suppressions": await get_em_repo(request).list_suppressions(_user_id(request), limit=limit, offset=offset)}


@router.post("/suppressions", status_code=201)
@require_auth
async def add_suppression(body: SuppressionCreate, request: Request) -> dict[str, Any]:
    _enabled()
    if body.reason not in SUPPRESSION_REASONS:
        raise HTTPException(status_code=422, detail=f"reason must be one of {', '.join(SUPPRESSION_REASONS)}")
    return await get_em_repo(request).suppress(_user_id(request), body.email, reason=body.reason, detail=body.detail)


@router.delete("/suppressions/{email}")
@require_auth
async def remove_suppression(email: str, request: Request) -> dict[str, Any]:
    _enabled()
    if not await get_em_repo(request).unsuppress(_user_id(request), email):
        raise HTTPException(status_code=404, detail="Suppression not found")
    return {"ok": True}
