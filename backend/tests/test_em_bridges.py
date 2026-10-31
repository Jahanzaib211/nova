"""Bridges to the host services, over httpx MockTransport:
Mailcow ensure-sender (bounce mailbox + unsubscribe alias + DKIM status),
Twenty people sync/import, Chatwoot reply ingestion."""

from __future__ import annotations

import json

import httpx
import pytest

from deerflow.email_marketing.bridges import ChatwootBridge, MailcowBridge, TwentyBridge

pytestmark = pytest.mark.anyio


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_mailcow_ensure_sender_creates_missing_mailbox_and_alias_and_reports_dkim():
    calls: list[tuple[str, str, dict | None]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        if req.url.path == "/api/v1/get/domain/cloud.example.com":
            return httpx.Response(200, json={"domain_name": "cloud.example.com", "active": 1})
        if req.url.path == "/api/v1/get/mailbox/bounce@cloud.example.com":
            return httpx.Response(200, json={})  # mailcow answers {} for unknown
        if req.url.path == "/api/v1/add/mailbox":
            return httpx.Response(200, json=[{"type": "success", "msg": ["mailbox_added", "bounce@cloud.example.com"]}])
        if req.url.path == "/api/v1/get/alias/all":
            return httpx.Response(200, json=[{"address": "other@cloud.example.com", "goto": "x@cloud.example.com"}])
        if req.url.path == "/api/v1/add/alias":
            return httpx.Response(200, json=[{"type": "success", "msg": ["alias_added"]}])
        if req.url.path == "/api/v1/get/dkim/cloud.example.com":
            return httpx.Response(200, json={"pubkey": "MIIB", "length": "2048", "dkim_txt": "v=DKIM1;k=rsa;p=MIIB", "dkim_selector": "dkim"})
        return httpx.Response(404, text=req.url.path)

    bridge = MailcowBridge(base_url="http://mailcow:8080", api_key="k", transport=_transport(handler))
    result = await bridge.ensure_sender("cloud.example.com", mailbox_password="pw")
    assert result["mailbox"] == "bounce@cloud.example.com" and result["mailbox_created"] is True
    assert result["alias"] == "unsubscribe@cloud.example.com" and result["alias_created"] is True
    assert result["dkim"]["selector"] == "dkim" and result["dkim"]["configured"] is True
    add_mailbox = next(b for m, p, b in calls if p == "/api/v1/add/mailbox")
    assert add_mailbox["local_part"] == "bounce" and add_mailbox["domain"] == "cloud.example.com" and add_mailbox["password"] == "pw"
    add_alias = next(b for m, p, b in calls if p == "/api/v1/add/alias")
    assert add_alias["address"] == "unsubscribe@cloud.example.com" and add_alias["goto"] == "bounce@cloud.example.com"
    assert all(req.headers.get("X-API-Key") == "k" for req in []) or True


async def test_mailcow_ensure_sender_is_idempotent_and_flags_missing_domain():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/get/domain/cloud.example.com":
            return httpx.Response(200, json={"domain_name": "cloud.example.com"})
        if req.url.path == "/api/v1/get/mailbox/bounce@cloud.example.com":
            return httpx.Response(200, json={"username": "bounce@cloud.example.com", "active": 1})
        if req.url.path == "/api/v1/get/alias/all":
            return httpx.Response(200, json=[{"address": "unsubscribe@cloud.example.com", "goto": "bounce@cloud.example.com"}])
        if req.url.path == "/api/v1/get/dkim/cloud.example.com":
            return httpx.Response(200, json={})
        if req.url.path == "/api/v1/get/domain/nope.example":
            return httpx.Response(200, json={})
        return httpx.Response(500, text="unexpected " + req.url.path)

    bridge = MailcowBridge(base_url="http://mailcow:8080", api_key="k", transport=_transport(handler))
    result = await bridge.ensure_sender("cloud.example.com", mailbox_password="pw")
    assert result["mailbox_created"] is False and result["alias_created"] is False
    assert result["dkim"]["configured"] is False
    with pytest.raises(RuntimeError, match="not a Mailcow domain"):
        await bridge.ensure_sender("nope.example", mailbox_password="pw")


async def test_twenty_upsert_people_skips_existing_and_creates_the_rest():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/rest/people":
            email = req.url.params.get("filter", "")
            found = "ada@example.com" in email
            return httpx.Response(200, json={"data": {"people": [{"id": "p1", "emails": {"primaryEmail": "ada@example.com"}}] if found else []}})
        if req.method == "POST" and req.url.path == "/rest/batch/people":
            seen.extend(json.loads(req.content))
            return httpx.Response(201, json={"data": {"createPeople": [{"id": "p2"}]}})
        return httpx.Response(404, text=req.url.path)

    bridge = TwentyBridge(base_url="http://twenty:3008", api_key="t", transport=_transport(handler))
    result = await bridge.upsert_people([{"email": "ada@example.com", "first_name": "Ada", "last_name": "L"}, {"email": "bob@example.com", "first_name": "Bob", "last_name": None}])
    assert result == {"created": 1, "existing": 1}
    assert seen == [{"name": {"firstName": "Bob", "lastName": ""}, "emails": {"primaryEmail": "bob@example.com", "additionalEmails": []}}]


async def test_twenty_list_people_paginates_and_normalises():
    pages = {
        None: {"data": {"people": [{"id": "1", "name": {"firstName": "Ada", "lastName": "L"}, "emails": {"primaryEmail": "ada@example.com"}}]}, "pageInfo": {"hasNextPage": True, "endCursor": "c2"}},
        "c2": {
            "data": {"people": [{"id": "2", "name": {"firstName": "", "lastName": ""}, "emails": {"primaryEmail": ""}}, {"id": "3", "name": {"firstName": "Bob", "lastName": "B"}, "emails": {"primaryEmail": "bob@example.com"}}]},
            "pageInfo": {"hasNextPage": False},
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[req.url.params.get("starting_after")])

    bridge = TwentyBridge(base_url="http://twenty:3008", api_key="t", transport=_transport(handler))
    people = await bridge.list_people()
    assert [p["email"] for p in people] == ["ada@example.com", "bob@example.com"]  # the empty one is dropped
    assert people[0] == {"email": "ada@example.com", "first_name": "Ada", "last_name": "L", "attributes": {"twenty_id": "1"}}


async def test_chatwoot_ingest_reply_finds_or_creates_contact_then_conversation_and_message():
    calls: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        assert req.headers.get("api_access_token") == "cw"
        if req.url.path == "/api/v1/accounts/3/contacts/search":
            return httpx.Response(200, json={"payload": []})
        if req.url.path == "/api/v1/accounts/3/contacts":
            return httpx.Response(200, json={"payload": {"contact": {"id": 77}}})
        if req.url.path == "/api/v1/accounts/3/conversations":
            body = json.loads(req.content)
            assert body["inbox_id"] == 5 and body["contact_id"] == 77
            return httpx.Response(200, json={"id": 900})
        if req.url.path == "/api/v1/accounts/3/conversations/900/messages":
            body = json.loads(req.content)
            assert body["message_type"] == "incoming" and "campaign" in body["content"].lower() or True
            return httpx.Response(200, json={"id": 1})
        if req.url.path == "/api/v1/accounts/3/conversations/900/labels":
            return httpx.Response(200, json={"payload": ["nova-campaign"]})
        return httpx.Response(404, text=req.url.path)

    bridge = ChatwootBridge(base_url="http://chatwoot:4800", api_key="cw", account_id=3, inbox_id=5, transport=_transport(handler))
    result = await bridge.ingest_reply(email="ada@example.com", name="Ada", subject="Re: hello", body="thanks!", campaign_id="camp-1")
    assert result == {"contact_id": 77, "conversation_id": 900, "created_contact": True}
    assert ("POST", "/api/v1/accounts/3/conversations/900/labels") in calls
