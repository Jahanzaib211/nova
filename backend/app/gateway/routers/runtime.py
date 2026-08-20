"""Read-only runtime config endpoint for the Settings UI.

Exposes the current ``config.yaml`` sections that affect runtime behavior:

- ``summarization`` — context-reduction middleware settings
- ``subagents`` — delegation system config
- ``guardrails`` — tool-call authorization settings

The endpoint is intentionally **read-only**. Mutating ``config.yaml`` is a
significant operation that requires file locking, schema validation, and a
graceful hot-reload (or restart for restart-required fields), which is out of
scope for this UI surface. Operators continue to edit ``config.yaml`` directly.

The endpoint reuses the standard session-cookie auth flow like other
agent-facing routes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.gateway.deps import get_config
from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


class SummarizationSummary(BaseModel):
    enabled: bool = False
    model_name: str | None = None
    trigger_type: str | None = None
    trigger_value: int | float | None = None
    keep_type: str | None = None
    keep_value: int | float | None = None


class SubagentsSummary(BaseModel):
    default_timeout_seconds: int | None = None
    max_turns: int | None = None
    custom_agents_count: int = 0


class GuardrailsSummary(BaseModel):
    enabled: bool = False
    fail_closed: bool = True
    passport: str | None = None
    provider_class: str | None = None


class RuntimeConfigResponse(BaseModel):
    summarization: SummarizationSummary = Field(default_factory=SummarizationSummary)
    subagents: SubagentsSummary = Field(default_factory=SubagentsSummary)
    guardrails: GuardrailsSummary = Field(default_factory=GuardrailsSummary)


def _safe_get(obj: Any, *path: str, default: Any = None) -> Any:
    """Walk an attribute path safely; return ``default`` if any segment is missing."""
    cur = obj
    for name in path:
        if cur is None:
            return default
        cur = getattr(cur, name, None)
    return cur if cur is not None else default


def _summarization_summary(config: AppConfig) -> SummarizationSummary:
    try:
        summ = getattr(config, "summarization", None)
        if summ is None:
            return SummarizationSummary()
        trigger = _safe_get(summ, "trigger", default=None)
        keep = _safe_get(summ, "keep", default=None)
        return SummarizationSummary(
            enabled=bool(_safe_get(summ, "enabled", default=False)),
            model_name=_safe_get(summ, "model_name", default=None),
            trigger_type=_safe_get(trigger, "type", default=None),
            trigger_value=_safe_get(trigger, "value", default=None),
            keep_type=_safe_get(keep, "type", default=None),
            keep_value=_safe_get(keep, "value", default=None),
        )
    except Exception as e:
        logger.debug("runtime: summarization parse failed: %s", e)
        return SummarizationSummary()


def _subagents_summary(config: AppConfig) -> SubagentsSummary:
    try:
        sub = getattr(config, "subagents", None)
        custom_agents = _safe_get(sub, "custom_agents", default=None) or {}
        return SubagentsSummary(
            default_timeout_seconds=_safe_get(sub, "timeout_seconds", default=None),
            max_turns=_safe_get(sub, "max_turns", default=None),
            custom_agents_count=len(custom_agents) if isinstance(custom_agents, dict) else 0,
        )
    except Exception as e:
        logger.debug("runtime: subagents parse failed: %s", e)
        return SubagentsSummary()


def _guardrails_summary(config: AppConfig) -> GuardrailsSummary:
    try:
        guards = getattr(config, "guardrails", None)
        if guards is None:
            return GuardrailsSummary()
        provider = _safe_get(guards, "provider", default=None)
        return GuardrailsSummary(
            enabled=bool(_safe_get(guards, "enabled", default=False)),
            fail_closed=bool(_safe_get(guards, "fail_closed", default=True)),
            passport=_safe_get(guards, "passport", default=None),
            provider_class=_safe_get(provider, "use", default=None),
        )
    except Exception as e:
        logger.debug("runtime: guardrails parse failed: %s", e)
        return GuardrailsSummary()


@router.get(
    "/config",
    response_model=RuntimeConfigResponse,
    summary="Read-only view of summarization, subagents, and guardrails settings",
)
async def get_runtime_config(
    config: AppConfig = Depends(get_config),
) -> RuntimeConfigResponse:
    """Returns the current values for the three operator-facing sections that
    the Settings UI surfaces. Edit ``config.yaml`` and reload to change them.
    """
    return RuntimeConfigResponse(
        summarization=_summarization_summary(config),
        subagents=_subagents_summary(config),
        guardrails=_guardrails_summary(config),
    )
