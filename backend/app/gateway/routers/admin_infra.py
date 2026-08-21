"""Admin-only read-only proxy for the sandbox provisioner's /api/infra/*
surface — pod/deployment/event listing, pod logs, and resource metrics.

Powers nova-ops's "Infra" dashboard (reached through nova-ops's BFF proxy,
which forwards /api/ops/infra/* to /api/v1/admin/infra/* with zero proxy
changes needed there). Gated by the same require_admin_user() as every
other endpoint in admin.py — reachable by a real admin session or the ops
console's service token.

Every endpoint here is a read. None can mutate the cluster. Only the pod
logs endpoint is audit-logged, since logs (unlike pod/deployment/event
metadata) can contain secrets or other sensitive content — the same class
of risk that already gates admin.py's view-conversation-messages endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.gateway import admin_ops, infra_client
from app.gateway.deps import require_admin_user
from app.gateway.routers.admin import _ADMIN_REQUIRED_DETAIL, _actor
from deerflow.config.app_config import get_app_config

router = APIRouter(prefix="/api/v1/admin/infra", tags=["admin"])


def _provisioner_url() -> str:
    sandbox_config = get_app_config().sandbox
    url = getattr(sandbox_config, "provisioner_url", None)
    if not url:
        # 501, not 503. A provisioner is optional: most deployments run sandboxes
        # locally and never set sandbox.provisioner_url, and for them this whole
        # feature simply does not exist. 503 is what a *configured* provisioner
        # returns when it is down (see the InfraClientError path below), so
        # reusing it here made the console unable to tell "you never set this up"
        # apart from "your cluster is broken" — and it rendered the former as a
        # red alarm on every Nova install that has no cluster at all.
        raise HTTPException(
            status_code=501,
            detail="No sandbox provisioner is configured (sandbox.provisioner_url).",
        )
    return url


async def _call(coro) -> dict:
    try:
        return await coro
    except infra_client.InfraClientError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


class PodLogsRequest(BaseModel):
    tail: int = 200
    container: str | None = None


@router.get("/pods")
async def get_pods(request: Request) -> dict:
    """List every Pod in the provisioner's namespace."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    result = await _call(infra_client.list_pods(_provisioner_url()))
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-infra-pods",
        target_user_id=None,
        payload={},
        request=request,
    )
    return result


@router.get("/deployments")
async def get_deployments(request: Request) -> dict:
    """List Deployments with rollout status."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    result = await _call(infra_client.list_deployments(_provisioner_url()))
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-infra-deployments",
        target_user_id=None,
        payload={},
        request=request,
    )
    return result


@router.get("/events")
async def get_events(request: Request, limit: int = Query(100, ge=1, le=500)) -> dict:
    """Recent namespace Events, newest first."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    result = await _call(infra_client.list_events(_provisioner_url(), limit=limit))
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-infra-events",
        target_user_id=None,
        payload={"limit": limit},
        request=request,
    )
    return result


@router.get("/metrics")
async def get_metrics(request: Request) -> dict:
    """Pod + node CPU/memory usage."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    result = await _call(infra_client.get_metrics(_provisioner_url()))
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-infra-metrics",
        target_user_id=None,
        payload={},
        request=request,
    )
    return result


@router.get("/pods/{pod_name}/logs")
async def get_pod_logs(
    request: Request,
    pod_name: str,
    tail: int = Query(200, ge=1, le=2000),
    container: str | None = Query(None),
) -> dict:
    """Tail logs for a Pod. Audit-logged: pod logs can contain secrets."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    result = await _call(infra_client.get_pod_logs(_provisioner_url(), pod_name, tail=tail, container=container))
    await admin_ops.record_audit(
        actor=_actor(request),
        action="view-pod-logs",
        target_user_id=None,
        payload={"pod": pod_name, "tail": tail, "container": container},
        request=request,
    )
    return result
