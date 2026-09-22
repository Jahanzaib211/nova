"""``/api/em/t/*`` and ``/api/em/u/*`` — public tracking and unsubscribe.

No session: the HMAC token in the path is the credential (see
``deerflow.email_marketing.tracking``). Registered as public paths in the
auth middleware and exempt from CSRF so a mail provider's one-click
``POST`` (RFC 8058) works with no cookie and no origin.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.gateway.deps import get_config, get_em_repo
from deerflow.email_marketing.tracking import verify_token, verify_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/em", tags=["email-marketing-public"])

# A 1x1 transparent GIF.
_PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
_NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}


async def _send_for(request: Request, purpose: str, token: str) -> dict[str, Any] | None:
    cfg = get_config().email_marketing
    if not cfg.enabled or not cfg.tracking_secret:
        return None
    send_id = verify_token(cfg.tracking_secret, purpose, token)
    if not send_id:
        return None
    return await get_em_repo(request).get_send(send_id)


@router.get("/t/o/{token}.gif", include_in_schema=False)
async def open_pixel(token: str, request: Request) -> Response:
    """Always a gif — a forged token must be indistinguishable from a real one."""
    send = await _send_for(request, "open", token)
    if send is not None:
        try:
            await get_em_repo(request).record_event(send["owner_user_id"], type="opened", campaign_id=send["campaign_id"], send_id=send["id"], contact_id=send["contact_id"], payload={"ua": request.headers.get("user-agent", "")[:200]})
        except Exception:
            logger.debug("open pixel: record failed", exc_info=True)
    return Response(content=_PIXEL, media_type="image/gif", headers=_NO_STORE)


@router.get("/t/c/{token}", include_in_schema=False)
async def click(token: str, request: Request, u: str = Query(min_length=1), sig: str = Query(default="")) -> Response:
    send = await _send_for(request, "click", token)
    if send is None:
        raise HTTPException(status_code=404, detail="Unknown link")
    cfg = get_config().email_marketing
    if not u.lower().startswith(("http://", "https://")) or not verify_url(cfg.tracking_secret, send["id"], u, sig):
        raise HTTPException(status_code=400, detail="Link signature does not match")
    try:
        await get_em_repo(request).record_event(
            send["owner_user_id"], type="clicked", campaign_id=send["campaign_id"], send_id=send["id"], contact_id=send["contact_id"], payload={"url": u[:2000], "ua": request.headers.get("user-agent", "")[:200]}
        )
    except Exception:
        logger.debug("click: record failed", exc_info=True)
    return RedirectResponse(url=u, status_code=302, headers=_NO_STORE)


_UNSUB_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Unsubscribed</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;color:#222">
<h1 style="font-size:1.4rem">You have been unsubscribed</h1>
<p>{email} will not receive further emails from this sender.</p>
</body></html>"""


async def _unsubscribe(request: Request, token: str, via: str) -> str:
    send = await _send_for(request, "unsub", token)
    if send is None:
        raise HTTPException(status_code=404, detail="Unknown link")
    repo = get_em_repo(request)
    owner = send["owner_user_id"]
    await repo.record_event(owner, type="unsubscribed", campaign_id=send["campaign_id"], send_id=send["id"], contact_id=send["contact_id"], payload={"via": via})
    await repo.unsubscribe_contact(owner, send["contact_id"], detail=f"{via} unsubscribe")
    await repo.record_event(owner, type="suppressed", campaign_id=send["campaign_id"], send_id=send["id"], contact_id=send["contact_id"], payload={"reason": "unsubscribe"})
    return send["email"]


@router.get("/u/{token}", include_in_schema=False)
async def unsubscribe_page(token: str, request: Request) -> HTMLResponse:
    email = await _unsubscribe(request, token, "link")
    return HTMLResponse(_UNSUB_HTML.format(email=email), headers=_NO_STORE)


@router.post("/u/{token}", include_in_schema=False)
async def unsubscribe_one_click(token: str, request: Request) -> Response:
    """RFC 8058 one-click: mail providers POST ``List-Unsubscribe=One-Click``."""
    await _unsubscribe(request, token, "one-click")
    return Response(status_code=200, headers=_NO_STORE)
