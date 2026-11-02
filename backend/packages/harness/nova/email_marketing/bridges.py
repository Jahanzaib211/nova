"""Bridges from email marketing to the machine's own services.

Built from first principles on each service's REST API, through the same
``host.docker.internal`` + ``nova-host-bridge`` path the integrations
registry probes. Every call is short (httpx, explicit timeouts) and every
bridge is optional: a missing API key makes the endpoint say so, never
crash a campaign.

- Mailcow: make the sender side real — the ``bounce@<domain>`` mailbox the
  VERP envelopes land in (Postfix's ``recipient_delimiter=+`` folds
  ``bounce+<send>@`` into it), an ``unsubscribe@<domain>`` alias pointing at
  it, and the domain's DKIM status.
- Twenty CRM: people ⇄ contacts by primary email.
- Chatwoot: a human reply to a campaign becomes a conversation in the
  configured inbox, labelled ``nova-campaign``.
"""

from __future__ import annotations

from typing import Any

import httpx

TIMEOUT = 10.0


class _Http:
    def __init__(self, base_url: str, headers: dict[str, str], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._headers = headers
        self._transport = transport

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base, headers=self._headers, timeout=TIMEOUT, transport=self._transport)


# ------------------------------------------------------------------ Mailcow


class MailcowBridge(_Http):
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(base_url, {"X-API-Key": api_key, "Content-Type": "application/json"}, transport)

    @staticmethod
    def _ok(response: httpx.Response) -> bool:
        if response.status_code >= 400:
            return False
        try:
            body = response.json()
        except ValueError:
            return False
        if isinstance(body, list):
            return all(str(item.get("type", "")).lower() == "success" for item in body if isinstance(item, dict))
        return True

    async def dkim(self, domain: str) -> dict[str, Any]:
        async with self.client() as c:
            r = await c.get(f"/api/v1/get/dkim/{domain}")
        body = r.json() if r.status_code < 400 else {}
        body = body if isinstance(body, dict) else {}
        return {"configured": bool(body.get("dkim_txt")), "selector": body.get("dkim_selector"), "length": body.get("length"), "txt": body.get("dkim_txt")}

    async def ensure_sender(self, domain: str, *, mailbox_password: str, local_part: str = "bounce", unsubscribe_local_part: str = "unsubscribe") -> dict[str, Any]:
        mailbox = f"{local_part}@{domain}"
        alias = f"{unsubscribe_local_part}@{domain}"
        async with self.client() as c:
            dom = await c.get(f"/api/v1/get/domain/{domain}")
            dom_body = dom.json() if dom.status_code < 400 else {}
            if not isinstance(dom_body, dict) or not dom_body.get("domain_name"):
                raise RuntimeError(f"{domain} is not a Mailcow domain on this server")
            mb = await c.get(f"/api/v1/get/mailbox/{mailbox}")
            mb_body = mb.json() if mb.status_code < 400 else {}
            mailbox_created = False
            if not (isinstance(mb_body, dict) and mb_body.get("username")):
                r = await c.post(
                    "/api/v1/add/mailbox",
                    json={
                        "local_part": local_part,
                        "domain": domain,
                        "name": "Nova bounces",
                        "password": mailbox_password,
                        "password2": mailbox_password,
                        "quota": "512",
                        "active": "1",
                        "force_pw_update": "0",
                        "tls_enforce_in": "0",
                        "tls_enforce_out": "0",
                    },
                )
                if not self._ok(r):
                    raise RuntimeError(f"Mailcow refused to add {mailbox}: {r.text[:300]}")
                mailbox_created = True
            aliases = await c.get("/api/v1/get/alias/all")
            existing = {a.get("address"): a.get("goto") for a in (aliases.json() if aliases.status_code < 400 else []) if isinstance(a, dict)}
            alias_created = False
            if existing.get(alias) != mailbox:
                r = await c.post("/api/v1/add/alias", json={"address": alias, "goto": mailbox, "active": "1"})
                if not self._ok(r):
                    raise RuntimeError(f"Mailcow refused to add alias {alias}: {r.text[:300]}")
                alias_created = True
        return {"domain": domain, "mailbox": mailbox, "mailbox_created": mailbox_created, "alias": alias, "alias_created": alias_created, "dkim": await self.dkim(domain)}


# ------------------------------------------------------------------- Twenty


class TwentyBridge(_Http):
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(base_url, {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, transport)

    async def _exists(self, c: httpx.AsyncClient, email: str) -> bool:
        r = await c.get("/rest/people", params={"filter": f"emails.primaryEmail[eq]:{email}", "limit": 1})
        if r.status_code >= 400:
            return False
        body = r.json()
        people = (body.get("data") or {}).get("people") or []
        return any(((p.get("emails") or {}).get("primaryEmail") or "").lower() == email.lower() for p in people)

    async def upsert_people(self, contacts: list[dict[str, Any]]) -> dict[str, int]:
        created = existing = 0
        async with self.client() as c:
            to_create = []
            for contact in contacts:
                if await self._exists(c, contact["email"]):
                    existing += 1
                    continue
                to_create.append({"name": {"firstName": contact.get("first_name") or "", "lastName": contact.get("last_name") or ""}, "emails": {"primaryEmail": contact["email"], "additionalEmails": []}})
            for i in range(0, len(to_create), 50):
                chunk = to_create[i : i + 50]
                r = await c.post("/rest/batch/people", json=chunk)
                if r.status_code >= 400:
                    raise RuntimeError(f"Twenty refused batch create: {r.text[:300]}")
                created += len(chunk)
        return {"created": created, "existing": existing}

    async def list_people(self, *, limit: int = 60, max_pages: int = 50) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        async with self.client() as c:
            for _ in range(max_pages):
                params: dict[str, Any] = {"limit": limit}
                if cursor:
                    params["starting_after"] = cursor
                r = await c.get("/rest/people", params=params)
                if r.status_code >= 400:
                    raise RuntimeError(f"Twenty list failed: {r.text[:300]}")
                body = r.json()
                for p in (body.get("data") or {}).get("people") or []:
                    email = ((p.get("emails") or {}).get("primaryEmail") or "").strip()
                    if not email:
                        continue
                    name = p.get("name") or {}
                    out.append({"email": email, "first_name": name.get("firstName") or None, "last_name": name.get("lastName") or None, "attributes": {"twenty_id": str(p.get("id", ""))}})
                info = body.get("pageInfo") or {}
                if not info.get("hasNextPage") or not info.get("endCursor"):
                    break
                cursor = info["endCursor"]
        return out


# ----------------------------------------------------------------- Chatwoot


class ChatwootBridge(_Http):
    def __init__(self, base_url: str, api_key: str, *, account_id: int, inbox_id: int, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(base_url, {"api_access_token": api_key, "Content-Type": "application/json"}, transport)
        self.account_id = account_id
        self.inbox_id = inbox_id

    async def ingest_reply(self, *, email: str, name: str | None, subject: str, body: str, campaign_id: str | None) -> dict[str, Any]:
        base = f"/api/v1/accounts/{self.account_id}"
        async with self.client() as c:
            r = await c.get(f"{base}/contacts/search", params={"q": email})
            found = (r.json().get("payload") or []) if r.status_code < 400 else []
            created_contact = False
            if found:
                contact_id = int(found[0]["id"])
            else:
                r = await c.post(f"{base}/contacts", json={"inbox_id": self.inbox_id, "name": name or email, "email": email})
                if r.status_code >= 400:
                    raise RuntimeError(f"Chatwoot refused to create the contact: {r.text[:300]}")
                payload = r.json().get("payload") or {}
                contact_id = int((payload.get("contact") or payload).get("id"))
                created_contact = True
            r = await c.post(f"{base}/conversations", json={"inbox_id": self.inbox_id, "contact_id": contact_id, "status": "open"})
            if r.status_code >= 400:
                raise RuntimeError(f"Chatwoot refused to open a conversation: {r.text[:300]}")
            conversation_id = int(r.json()["id"])
            content = f"{subject}\n\n{body}".strip()
            if campaign_id:
                content = f"[campaign {campaign_id}] {content}"
            r = await c.post(f"{base}/conversations/{conversation_id}/messages", json={"content": content[:10000], "message_type": "incoming", "private": False})
            if r.status_code >= 400:
                raise RuntimeError(f"Chatwoot refused the message: {r.text[:300]}")
            await c.post(f"{base}/conversations/{conversation_id}/labels", json={"labels": ["nova-campaign"]})
        return {"contact_id": contact_id, "conversation_id": conversation_id, "created_contact": created_contact}
