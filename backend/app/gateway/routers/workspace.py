"""Workspace Intelligence endpoints (Phase C10 prerequisite).

Thread-scoped views over the Workspace Intelligence Kernel for the frontend
panels (Files→Workspace card, symbol tree, command discovery, Activity):

  POST /api/workspace/{thread_id}/index     — build/refresh the snapshot
  GET  /api/workspace/{thread_id}/snapshot  — snapshot summary (+detail)
  POST /api/workspace/{thread_id}/plan      — read-only execution planning
  GET  /api/workspace/{thread_id}/commands  — detected command registry
  GET  /api/workspace/{thread_id}/symbols   — symbol search (?q= prefix)
  POST /api/workspace/{thread_id}/impact    — blast radius of changed files
  GET  /api/workspace/{thread_id}/metrics   — kernel metrics snapshot

Security posture:
- Feature-flagged: 403 unless ``workspace.intelligence_enabled`` in
  config.yaml (default off — kernel stays dormant).
- The scanned root is ALWAYS the caller's own thread workspace directory,
  derived server-side from the authenticated user + thread_id. Callers can
  never supply a filesystem path.
- ``@require_permission(..., owner_check=True)`` prevents cross-tenant
  access by thread_id guessing.
- No plan-execution endpoint: ``execute_plan`` is intentionally not exposed
  over HTTP.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from deerflow.config import get_app_config
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.services.container import service_container

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

_MAX_SYMBOL_RESULTS = 200
_MAX_IMPACT_FILES = 100


def _require_enabled() -> None:
    config = get_app_config()
    if not getattr(config.workspace, "intelligence_enabled", False):
        raise HTTPException(status_code=403, detail="Workspace intelligence is not enabled")


def _workspace_root(thread_id: str) -> str:
    """Resolve the caller's thread workspace directory (server-side only)."""
    try:
        user_id = get_effective_user_id()
    except Exception:
        user_id = None
    root = get_paths().sandbox_work_dir(thread_id, user_id=user_id)
    if not root.exists():
        raise HTTPException(status_code=404, detail="Thread workspace not found")
    return str(root)


def _service():
    return service_container.workspace_intelligence_service


class IndexRequest(BaseModel):
    force_refresh: bool = False


class PlanRequest(BaseModel):
    kind: Literal["search", "run"]
    query: str = Field(default="", max_length=500)
    command_name: str = Field(default="", max_length=200)
    project_id: str | None = None
    file_pattern: str | None = Field(default=None, max_length=200)


class ImpactRequest(BaseModel):
    files: list[str] = Field(default_factory=list, max_length=_MAX_IMPACT_FILES)


def _snapshot_summary(snapshot) -> dict[str, Any]:
    fingerprint = snapshot.fingerprint
    return {
        "thread_workspace": snapshot.thread_id,
        "repo_kind": getattr(fingerprint.kind, "value", str(fingerprint.kind)),
        "primary_language": fingerprint.primary_language,
        "is_monorepo": fingerprint.is_monorepo,
        "project_count": snapshot.project_count,
        "symbol_count": snapshot.symbol_count,
        "command_count": snapshot.command_count,
        "node_count": snapshot.node_count,
        "edge_count": snapshot.edge_count,
        "traversal_count": snapshot.traversal_count,
        "duration_ms": snapshot.duration_ms,
        "projects": [
            {
                "project_id": p.project_id,
                "name": p.name,
                "kind": getattr(p.kind, "value", str(p.kind)),
                "root_path": p.root_path,
            }
            for p in snapshot.projects
        ],
    }


@router.post("/{thread_id}/index")
@require_permission("threads", "write", owner_check=True)
async def index_workspace(thread_id: str, request: Request, body: IndexRequest | None = None) -> dict[str, Any]:
    """Build or refresh the workspace snapshot for a thread."""
    _require_enabled()
    root = _workspace_root(thread_id)
    force_refresh = bool(body and body.force_refresh)
    snapshot = await _service().scan_async(root, force_refresh=force_refresh)
    return {"status": "ok", "snapshot": _snapshot_summary(snapshot)}


@router.get("/{thread_id}/snapshot")
@require_permission("threads", "read", owner_check=True)
async def get_snapshot(thread_id: str, request: Request) -> dict[str, Any]:
    """Return the cached snapshot summary, or 404 if not yet indexed."""
    _require_enabled()
    root = _workspace_root(thread_id)
    snapshot = _service().get_snapshot(root)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Workspace not indexed yet")
    return {"snapshot": _snapshot_summary(snapshot)}


@router.post("/{thread_id}/plan")
@require_permission("threads", "read", owner_check=True)
async def build_plan(thread_id: str, request: Request, body: PlanRequest) -> dict[str, Any]:
    """Build (but never execute) an execution plan for a search or command."""
    _require_enabled()
    _workspace_root(thread_id)
    service = _service()
    if body.kind == "search":
        if not body.query:
            raise HTTPException(status_code=422, detail="query is required for kind=search")
        result = service.plan_search(body.query, body.project_id, body.file_pattern)
    else:
        if not body.command_name:
            raise HTTPException(status_code=422, detail="command_name is required for kind=run")
        result = service.plan_run(body.command_name, body.project_id)

    plan = result.plan
    return {
        "plan": None
        if plan is None
        else {
            "plan_id": plan.plan_id,
            "goal": plan.goal,
            "risk_level": getattr(plan.risk_level, "value", str(plan.risk_level)),
            "steps": [
                {
                    "step_id": s.step_id,
                    "kind": getattr(s.kind, "value", str(s.kind)),
                    "description": s.description,
                    "argv": list(s.argv),
                    "depends_on": list(s.depends_on),
                }
                for s in plan.steps
            ],
        },
        "symbols_found": list(getattr(result, "symbols_found", ()) or ()),
        "warnings": list(getattr(result, "warnings", ()) or ()),
    }


@router.get("/{thread_id}/commands")
@require_permission("threads", "read", owner_check=True)
async def list_commands(thread_id: str, request: Request) -> dict[str, Any]:
    """List commands detected in the thread workspace."""
    _require_enabled()
    root = _workspace_root(thread_id)
    snapshot = _service().get_snapshot(root)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Workspace not indexed yet")
    return {
        "commands": [
            {
                "name": c.name,
                "kind": getattr(c.kind, "value", str(c.kind)),
                "project_id": c.project_id,
                "argv": list(c.argv),
                "description": c.description,
            }
            for c in snapshot.commands
        ]
    }


@router.get("/{thread_id}/symbols")
@require_permission("threads", "read", owner_check=True)
async def search_symbols(
    thread_id: str,
    request: Request,
    q: str = Query(default="", max_length=200),
) -> dict[str, Any]:
    """Prefix-search symbols in the thread workspace snapshot."""
    _require_enabled()
    root = _workspace_root(thread_id)
    snapshot = _service().get_snapshot(root)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Workspace not indexed yet")

    needle = q.lower()
    matches = [s for s in snapshot.symbols if not needle or s.name.lower().startswith(needle)]
    return {
        "query": q,
        "total": len(matches),
        "symbols": [
            {
                "name": s.name,
                "kind": getattr(s.kind, "value", str(s.kind)),
                "fqn": s.fqn,
                "file_path": s.file_path,
                "language": s.language,
            }
            for s in matches[:_MAX_SYMBOL_RESULTS]
        ],
    }


@router.post("/{thread_id}/impact")
@require_permission("threads", "read", owner_check=True)
async def analyze_impact(thread_id: str, request: Request, body: ImpactRequest) -> dict[str, Any]:
    """Return symbols/projects/commands affected by changes to the given files."""
    _require_enabled()
    root = _workspace_root(thread_id)
    return _service().analyze_impact(root, body.files)


@router.get("/{thread_id}/metrics")
@require_permission("threads", "read", owner_check=True)
async def get_metrics(thread_id: str, request: Request) -> dict[str, Any]:
    """Return workspace-kernel metrics (scan counts, cache hit ratio, ...)."""
    _require_enabled()
    _workspace_root(thread_id)
    metrics = _service()._metrics
    snapshot_fn = getattr(metrics, "snapshot", None)
    return {"metrics": snapshot_fn() if callable(snapshot_fn) else {}}
