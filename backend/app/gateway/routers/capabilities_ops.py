"""``/api/capabilities/ops`` — the generic operation surface.

Every capability module's operations are reachable here with one shape
(``POST /api/capabilities/ops/{name}`` with the op's input as JSON), which
is what the generated frontend client and Nova's MCP server both use. The
dedicated routers (``/api/jobs`` …) remain for their streaming and
file-shaped endpoints; the registry is the source of truth for *what*
exists.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.authz import require_auth
from deerflow.capabilities import CapabilityRegistry, OpContext, OperationDenied, OperationNotFound, get_registry
from deerflow.capabilities.modules.features import compute_flags

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


def registry_for(request: Request) -> CapabilityRegistry:
    reg = getattr(request.app.state, "capability_registry", None)
    return reg if reg is not None else get_registry()


def context_for(request: Request, *, surface: str = "api") -> OpContext:
    user = request.state.user
    return OpContext(user_id=str(user.id), is_admin=getattr(user, "system_role", None) == "admin", surface=surface)


class OpsListResponse(BaseModel):
    snapshot: dict[str, Any]
    status: dict[str, dict[str, Any]]
    flags: dict[str, bool]


class InvokeResponse(BaseModel):
    name: str
    result: dict[str, Any] = Field(default_factory=dict)


@router.get("/ops", response_model=OpsListResponse, summary="Every capability operation, module status and feature flags")
@require_auth
async def list_ops(request: Request) -> OpsListResponse:
    reg = registry_for(request)
    statuses = await reg.statuses()
    return OpsListResponse(
        snapshot=reg.snapshot(),
        status={k: {"configured": v.configured, "healthy": v.healthy, "detail": v.detail} for k, v in statuses.items()},
        flags=compute_flags(),
    )


@router.post("/ops/{name}", response_model=InvokeResponse, summary="Invoke one capability operation")
@require_auth
async def invoke_op(name: str, request: Request) -> InvokeResponse:
    reg = registry_for(request)
    try:
        payload = await request.json() if await request.body() else {}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    ctx = context_for(request)
    try:
        result = await reg.invoke(name, ctx, payload)
    except OperationNotFound:
        raise HTTPException(status_code=404, detail=f"unknown operation {name!r}") from None
    except OperationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("capability %s failed for user %s: %s", name, ctx.user_id, exc)
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc
    return InvokeResponse(name=name, result=result)
