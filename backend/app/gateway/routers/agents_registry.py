"""``GET /api/agents/registry`` — every agent Nova can run, with live counts."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from app.gateway.authz import AuthContext, require_auth
from app.gateway.deps import get_config
from deerflow.agents_registry.registry import build_registry, live_counts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/registry", summary="Every agent Nova can run, with queued/running counts")
@require_auth
async def agents_registry(request: Request) -> dict[str, Any]:
    auth: AuthContext = request.state.auth
    owner = str(auth.user.id) if auth.user is not None else None
    config = get_config()
    custom: list[Any] = []
    try:
        from deerflow.config.agents_config import list_custom_agents

        custom = list_custom_agents(user_id=owner)
    except Exception as exc:
        logger.debug("registry: custom agents unavailable: %s", exc)
    repo = getattr(request.app.state, "jobs_repo", None)
    counts = await live_counts(repo, owner)
    return {
        "async_enabled": bool(getattr(config.subagents, "async_enabled", False)) and bool(getattr(config.jobs, "enabled", False)),
        "agents": build_registry(config, custom_agents=custom, counts=counts),
    }
