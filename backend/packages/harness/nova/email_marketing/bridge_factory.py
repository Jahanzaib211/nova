"""Build bridges from config: URLs and API keys come from the integrations
registry's service entries, the switches from ``email_marketing.bridges``."""

from __future__ import annotations

from typing import Any

from deerflow.config.email_marketing_config import EmailMarketingConfig
from deerflow.config.integrations_config import IntegrationsConfig
from deerflow.email_marketing.bridges import ChatwootBridge, MailcowBridge, TwentyBridge


def bridge_status(em: EmailMarketingConfig, integrations: IntegrationsConfig) -> dict[str, dict[str, Any]]:
    """For each bridge: enabled? configured (service + key)? why not?"""
    out: dict[str, dict[str, Any]] = {}
    for name, enabled in (("mailcow", em.bridges.mailcow), ("twenty", em.bridges.twenty), ("chatwoot", em.bridges.chatwoot)):
        svc = integrations.services.get(name)
        problems: list[str] = []
        if not enabled:
            problems.append(f"email_marketing.bridges.{name} is false")
        if svc is None:
            problems.append(f"integrations.services.{name} is not configured")
        elif not svc.enabled:
            problems.append(f"integrations.services.{name} is disabled")
        elif not svc.resolve_api_key():
            problems.append(f"{svc.api_key_env or 'api_key_env'} is not set")
        if name == "chatwoot" and enabled and not em.bridges.chatwoot_inbox_id:
            problems.append("email_marketing.bridges.chatwoot_inbox_id is not set")
        out[name] = {"enabled": enabled, "configured": not problems, "problems": problems, "endpoint": svc.url if svc else None}
    return out


def mailcow_bridge(em: EmailMarketingConfig, integrations: IntegrationsConfig, **kw: Any) -> MailcowBridge | None:
    svc = integrations.services.get("mailcow")
    key = svc.resolve_api_key() if svc and svc.enabled and em.bridges.mailcow else None
    return MailcowBridge(svc.url, key, **kw) if svc and key else None


def twenty_bridge(em: EmailMarketingConfig, integrations: IntegrationsConfig, **kw: Any) -> TwentyBridge | None:
    svc = integrations.services.get("twenty")
    key = svc.resolve_api_key() if svc and svc.enabled and em.bridges.twenty else None
    return TwentyBridge(svc.url, key, **kw) if svc and key else None


def chatwoot_bridge(em: EmailMarketingConfig, integrations: IntegrationsConfig, **kw: Any) -> ChatwootBridge | None:
    svc = integrations.services.get("chatwoot")
    key = svc.resolve_api_key() if svc and svc.enabled and em.bridges.chatwoot else None
    if not (svc and key and em.bridges.chatwoot_inbox_id):
        return None
    return ChatwootBridge(svc.url, key, account_id=em.bridges.chatwoot_account_id, inbox_id=em.bridges.chatwoot_inbox_id, **kw)
