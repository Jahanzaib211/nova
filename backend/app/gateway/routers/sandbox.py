"""Sandbox observation endpoints.

Three lightweight endpoints that expose sandbox state to the frontend
for the "Agent's Computer" right panel:

  GET /api/sandbox/logs   — SSE stream of bash command output for a thread
  GET /api/sandbox/todo   — Parsed todo list from LangGraph checkpoint state
  GET /api/sandbox/status — Current tool label (idle / terminal / browser / editor)
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import socket
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

from app.gateway.deps import get_checkpointer
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id, reset_current_user, set_current_user
from deerflow.sandbox.dev_server import (
    DEFAULT_LABEL,
    adopt_handle,
    discover_live_preview,
    get_dev_server,
    list_dev_servers,
    port_alive,
    register_external_dev_server,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])

_KEEPALIVE_INTERVAL = 15  # seconds between keepalive pings
_POLL_INTERVAL = 0.5  # seconds between log-file tail polls

# CSP for the sandbox's framed panes (ttyd/noVNC at /appview and agent-built dev
# servers at /preview). They are iframed same-origin by the app shell, and must
# load their own assets AND authenticate against the gateway. An opaque sandbox
# origin cannot do either: its sub-resource requests arrive cookie-less, so the
# global auth middleware 401s every asset and the ws same-origin guard rejects
# `Origin: null` — blank terminal/VNC/preview on every deployment, invisible
# under `make dev` where auth is disabled. `allow-same-origin` keeps the sandbox
# boundary (script cannot reach the parent frame's DOM) while letting the framed
# content carry the session cookie like any other same-origin page.
_FRAMED_SANDBOX_CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals allow-same-origin"
# The generic absproxy is not framed by the app shell, so it keeps the stricter
# opaque-origin CSP (defense in depth for arbitrary in-container ports).
_OPAQUE_SANDBOX_CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals"


def _is_port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    """Sync TCP liveness probe used at registration time.

    Mirrors ``dev_server.port_alive`` but stays synchronous so the endpoint can
    fail-fast (HTTP 400) when a caller advertises a port nothing is bound to.
    """
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _caller_owns_thread(thread_id: str) -> bool:
    """True if the authenticated caller owns this thread.

    Mirrors the user-scoped path resolution used by the other sandbox
    endpoints: a thread's data lives under the owner's user bucket, so if
    the thread dir resolves+exists for the effective user, they own it.
    Prevents IDOR / cross-tenant access to dev servers.
    """
    try:
        user_id = get_effective_user_id()
    except Exception:
        user_id = None
    try:
        return get_paths().thread_dir(thread_id, user_id=user_id).exists()
    except Exception:
        return False


def _sandbox_log_path(thread_id: str, user_id: str | None = None) -> Path:
    """Return the host-side path for the per-thread sandbox execution log."""
    paths = get_paths()
    return paths.thread_dir(thread_id, user_id=user_id) / "sandbox.log"


def _sandbox_status_path(thread_id: str, user_id: str | None = None) -> Path:
    paths = get_paths()
    return paths.thread_dir(thread_id, user_id=user_id) / "sandbox_status.json"


# ──────────────────────────────────────────────────────────
# GET /api/sandbox/logs
# ──────────────────────────────────────────────────────────


@router.get("/logs")
async def stream_sandbox_logs(
    thread_id: str,
    request: Request,
) -> StreamingResponse:
    """SSE stream of bash command output for *thread_id*.

    Tails the per-thread ``sandbox.log`` file.  When the file does not yet
    exist, the endpoint waits until it appears (or until the client
    disconnects).  A keepalive ``[KEEPALIVE]`` comment is emitted every 15 s
    so reverse-proxies don't close idle connections.
    """
    user_id = get_effective_user_id()
    log_path = _sandbox_log_path(thread_id, user_id=user_id)

    async def event_generator():
        seek_pos = 0
        idle_ticks = 0

        while True:
            if await request.is_disconnected():
                break

            if log_path.exists():
                try:
                    with open(log_path, encoding="utf-8", errors="replace") as fh:
                        fh.seek(seek_pos)
                        chunk = fh.read()
                        seek_pos = fh.tell()
                    if chunk:
                        for line in chunk.splitlines():
                            if line:
                                yield f"data: {line}\n\n"
                        idle_ticks = 0
                except OSError:
                    pass

            idle_ticks += 1
            if idle_ticks >= int(_KEEPALIVE_INTERVAL / _POLL_INTERVAL):
                yield "data: [KEEPALIVE]\n\n"
                idle_ticks = 0

            await asyncio.sleep(_POLL_INTERVAL)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ──────────────────────────────────────────────────────────
# GET /api/sandbox/todo
# ──────────────────────────────────────────────────────────


@router.get("/todo")
async def get_sandbox_todo(
    thread_id: str,
    request: Request,
    checkpointer=Depends(get_checkpointer),
) -> dict:
    """Return the current todo list from the LangGraph checkpoint.

    Reads directly from the checkpointer so the data is always fresh
    without requiring a file write from the agent.  Also checks for a
    ``todo.md`` file written by the TodoMiddleware (if present).
    """
    # Ownership guard: the checkpoint fallback reads by raw thread_id, so without
    # this a caller could read another tenant's todos. Mirrors the dev endpoints.
    if not _caller_owns_thread(thread_id):
        return {"content": "", "todos": []}

    # Try to read from the sandbox todo.md file first (written by TodoMiddleware)
    user_id = get_effective_user_id()
    paths = get_paths()
    todo_md_path = paths.sandbox_work_dir(thread_id, user_id=user_id) / "todo.md"

    todos: list[dict] = []
    content = ""

    if todo_md_path.exists():
        try:
            content = todo_md_path.read_text(encoding="utf-8")
            todos = _parse_todo_md(content)
        except OSError:
            pass

    # Fall back to LangGraph checkpoint state
    if not todos:
        try:
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint_tuple = await checkpointer.aget_tuple(config)
            if checkpoint_tuple is not None:
                checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
                channel_values = checkpoint.get("channel_values", {})
                raw_todos = channel_values.get("todos") or []
                todos = _normalize_todos(raw_todos)
                content = _format_todo_md(todos)
        except Exception:
            logger.exception("Failed to read todos from checkpointer for thread %s", thread_id.replace("\n", "").replace("\r", ""))

    return {"content": content, "todos": todos}


def _parse_todo_md(content: str) -> list[dict]:
    """Parse markdown checkbox list into todo dicts."""
    todos = []
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("- [x]") or line.startswith("- [X]"):
            todos.append({"description": line[5:].strip(), "status": "completed"})
        elif line.startswith("- [ ]"):
            todos.append({"description": line[5:].strip(), "status": "pending"})
        elif line.startswith("- [~]"):
            todos.append({"description": line[5:].strip(), "status": "in_progress"})
    return todos


def _normalize_todos(raw: list) -> list[dict]:
    """Normalize Todo objects or dicts from LangGraph state."""
    result = []
    for t in raw:
        if isinstance(t, dict):
            description = t.get("content") or t.get("description") or ""
            status = t.get("status") or "pending"
        else:
            description = getattr(t, "content", "") or getattr(t, "description", "") or str(t)
            status = getattr(t, "status", "pending") or "pending"
        result.append({"description": description, "status": status})
    return result


def _format_todo_md(todos: list[dict]) -> str:
    lines = ["# Task Progress\n"]
    for t in todos:
        status = t.get("status", "pending")
        desc = t.get("description", "")
        if status == "completed":
            cb = "[x]"
        elif status == "in_progress":
            cb = "[~]"
        else:
            cb = "[ ]"
        lines.append(f"- {cb} {desc}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────
# GET /api/sandbox/status
# ──────────────────────────────────────────────────────────

import json  # noqa: E402

_TOOL_LABELS: dict[str, str] = {
    "bash": "is using Terminal",
    "execute_command": "is using Terminal",
    "str_replace": "is using Editor",
    "write_file": "is using Editor",
    "read_file": "is using Editor",
    "ls": "is using Editor",
    "browser": "is using Browser",
    "web_search": "is using Browser",
    "tavily_search": "is using Browser",
}


@router.get("/status")
async def get_sandbox_status(
    thread_id: str,
    request: Request,
) -> dict:
    """Return the current tool label for *thread_id*.

    Reads ``sandbox_status.json`` written by the bash tool on each invocation.
    Returns ``{tool: None, label: "idle"}`` when no file exists.
    """
    user_id = get_effective_user_id()
    status_path = _sandbox_status_path(thread_id, user_id=user_id)

    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            tool = data.get("tool")
            label = _TOOL_LABELS.get(tool or "", "idle") if tool else "idle"
            return {"tool": tool, "label": label}
        except Exception:
            pass

    return {"tool": None, "label": "idle"}


# ──────────────────────────────────────────────────────────
# GET /api/sandbox/file
# ──────────────────────────────────────────────────────────


@router.get("/file")
async def get_sandbox_file(
    thread_id: str,
    path: str,
    request: Request,
) -> dict:
    """Return raw text content of a sandbox file for live preview.

    Only serves files inside the thread's virtual /mnt/user-data/ tree.
    Returns JSON so the frontend can use srcdoc for HTML iframe rendering
    without the XSS-protection download forced by the artifacts endpoint.
    """
    user_id = get_effective_user_id()
    paths = get_paths()

    if not path.startswith("/mnt/user-data/"):
        return {"content": "", "path": path, "exists": False, "size": 0}

    try:
        host_path = paths.resolve_virtual_path(thread_id, path, user_id=user_id)
    except ValueError:
        return {"content": "", "path": path, "exists": False, "size": 0}

    if not host_path.exists() or not host_path.is_file():
        return {"content": "", "path": path, "exists": False, "size": 0}

    try:
        content = host_path.read_text(encoding="utf-8", errors="replace")
        return {"content": content, "path": path, "exists": True, "size": host_path.stat().st_size}
    except Exception:
        return {"content": "", "path": path, "exists": False, "size": 0}


# ──────────────────────────────────────────────────────────
# GET /api/sandbox/files
# ──────────────────────────────────────────────────────────


@router.get("/files")
async def list_sandbox_files(
    thread_id: str,
    request: Request,
) -> dict:
    """List all files in the thread's workspace directory.

    Returns a flat list of files with virtual paths, sizes, and modified times,
    sorted newest-first. Skips hidden files and node_modules.
    """
    import datetime as _dt

    user_id = get_effective_user_id()
    paths = get_paths()
    # Agents write deliverables to outputs/ far more often than workspace/;
    # listing only workspace/ made entire runs' output invisible in the UI.
    roots = [
        (paths.sandbox_work_dir(thread_id, user_id=user_id), "/mnt/user-data/workspace"),
        (paths.sandbox_outputs_dir(thread_id, user_id=user_id), "/mnt/user-data/outputs"),
        (paths.sandbox_uploads_dir(thread_id, user_id=user_id), "/mnt/user-data/uploads"),
    ]

    files: list[dict] = []
    _SKIP = {"node_modules", ".git", "__pycache__", ".venv", ".cache"}

    for root, virtual_prefix in roots:
        if not root.exists():
            continue
        try:
            for host_path in root.rglob("*"):
                if host_path.is_dir():
                    continue
                # Skip hidden files and blacklisted directories
                parts = host_path.relative_to(root).parts
                if any(p.startswith(".") or p in _SKIP for p in parts):
                    continue
                try:
                    stat = host_path.stat()
                    rel = host_path.relative_to(root)
                    files.append(
                        {
                            "path": str(host_path),
                            "virtual_path": f"{virtual_prefix}/{rel.as_posix()}",
                            "name": host_path.name,
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                            "modified": _dt.datetime.fromtimestamp(stat.st_mtime).strftime("%H:%M:%S"),
                        }
                    )
                except OSError:
                    continue
        except Exception:
            continue

    # Numeric mtime, not the "%H:%M:%S" string — string sort breaks across midnight.
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return {"files": files}


# ──────────────────────────────────────────────────────────
# GET /api/sandbox/download-zip
# ──────────────────────────────────────────────────────────


@router.get("/download-zip")
async def download_sandbox_zip(
    thread_id: str,
    request: Request,
) -> StreamingResponse:
    """Download the entire workspace as a zip archive.

    Excludes node_modules, .next, .git, and other build artifacts.
    Returns a streaming zip file response.
    """
    import datetime as _dt

    user_id = get_effective_user_id()
    paths = get_paths()
    # Workspace files sit at the archive root (legacy layout); outputs/ and
    # uploads/ are prefixed with their mount folder — same visibility rule
    # as /api/sandbox/files, otherwise agent deliverables export as an empty zip.
    roots = [
        (paths.sandbox_work_dir(thread_id, user_id=user_id), ""),
        (paths.sandbox_outputs_dir(thread_id, user_id=user_id), "outputs"),
        (paths.sandbox_uploads_dir(thread_id, user_id=user_id), "uploads"),
    ]

    _SKIP = {"node_modules", ".git", "__pycache__", ".venv", ".cache", "dist", ".next", ".turbo"}

    def build_zip() -> bytes:
        buf = io.BytesIO()
        # Guards against two files claiming one archive path. Flattening the
        # workspace to the archive root puts the workspace's own `outputs/`
        # directory in the same namespace as the outputs mount, so
        # workspace/outputs/report.md and outputs/report.md both resolved to
        # "outputs/report.md". zipfile happily writes both (only a UserWarning),
        # and every extractor keeps whichever lands last — so one of the two
        # files was silently unrecoverable from the archive.
        seen: set[str] = set()

        def unique(arcname: str) -> str:
            if arcname not in seen:
                seen.add(arcname)
                return arcname
            stem, dot, ext = arcname.rpartition(".")
            base, suffix = (stem, f".{ext}") if dot else (arcname, "")
            n = 2
            while f"{base} ({n}){suffix}" in seen:
                n += 1
            resolved = f"{base} ({n}){suffix}"
            seen.add(resolved)
            return resolved

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for root, arc_prefix in roots:
                if not root.exists():
                    continue
                for host_path in root.rglob("*"):
                    if host_path.is_dir():
                        continue
                    parts = host_path.relative_to(root).parts
                    if any(p.startswith(".") or p in _SKIP for p in parts):
                        continue
                    try:
                        rel = host_path.relative_to(root)
                        # as_posix() on both branches: str(rel) uses the OS
                        # separator, which writes backslash-separated entry
                        # names on Windows and violates the ZIP spec.
                        arcname = f"{arc_prefix}/{rel.as_posix()}" if arc_prefix else rel.as_posix()
                        zf.write(host_path, arcname=unique(arcname))
                    except (OSError, PermissionError):
                        continue
        return buf.getvalue()

    zip_bytes = await asyncio.to_thread(build_zip)
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"workspace-{ts}.zip"

    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(zip_bytes)),
        },
    )


# ──────────────────────────────────────────────────────────
# Live dev server: status, logs, and HTTP proxy
# ──────────────────────────────────────────────────────────


def _preview_prefix(thread_id: str, label: str) -> str:
    """Proxy URL prefix for a (thread, label). The default ``app`` label keeps the
    original label-less path so the single-server UX is byte-identical; extra
    labels get a distinct, unambiguous base (avoids the `{path:path}` catch-all)."""
    if label == DEFAULT_LABEL:
        return f"/api/sandbox/preview/{thread_id}"
    return f"/api/sandbox/lpreview/{thread_id}/{label}"


# Liveness authority for dev servers: `port_alive` (imported from dev_server).
# It stays true as long as the server actually listens, regardless of whether the
# log-tail poller is still alive (fixes 'preview showed once then went blank').


def _mark_sandbox_active(thread_id: str) -> None:
    """Refresh the sandbox's idle timer for user-visible traffic.

    The preview/appview proxies are hot paths that must not block on the
    provider, so this is best-effort: a sandbox being actively watched (dev
    server preview, terminal/VNC pane) should not be reaped by the idle checker,
    whose timer is only bumped by acquire/get otherwise.
    """
    try:
        from deerflow.sandbox import get_sandbox_provider

        mark = getattr(get_sandbox_provider(), "mark_active", None)
        if mark is not None:
            mark(thread_id)
    except Exception:
        pass


@router.get("/dev-status")
async def dev_status(thread_id: str, label: str = DEFAULT_LABEL) -> dict:
    """Return the live dev server status for a thread (optionally a labeled one)."""
    if not _caller_owns_thread(thread_id):
        return {
            "running": False,
            "status": "stopped",
            "port": None,
            "host": None,
            "url": None,
            "absproxy_url": None,
        }
    handle = get_dev_server(thread_id, label)
    if handle is None or handle.status not in ("starting", "ready"):
        # Missing/stale handle — adopt a live server on the published ports if
        # one exists, so the preview recovers without the agent restarting it.
        found = await discover_live_preview(thread_id)
        if found is not None:
            container_port, host, host_port = found
            handle = adopt_handle(thread_id, label, container_port, host, host_port)
    if handle is None:
        return {
            "running": False,
            "status": "stopped",
            "port": None,
            "host": None,
            "url": None,
            "absproxy_url": None,
        }
    # Liveness is the port, not the poller: a reachable port means the preview
    # works even if the log tail died. While still "starting", trust the status
    # so the UI shows "compiling…" before the port is up.
    alive = await port_alive(handle.host, handle.port)
    running = alive or handle.status == "starting"
    status = handle.status
    if alive and status not in ("ready", "starting"):
        status = "ready"  # server is up even if the poller never saw a ready marker
    return {
        "running": running,
        "status": status,
        "host": handle.host,
        "port": handle.port,
        "label": handle.label,
        "compiles": handle.compiles,
        "url": f"{_preview_prefix(thread_id, handle.label)}/" if running else None,
        # Generic absproxy URL is the safety net the Browser tab can fall back
        # to when the canonical preview proxy fails (e.g. a dev server started
        # outside ``start_dev_server`` whose host:port the gateway cannot
        # reach via the in-container preview port).
        "absproxy_url": (
            f"/api/sandbox/absproxy/{thread_id}/{handle.port}/"
            if running and handle.port
            else None
        ),
    }


@router.post("/dev-start")
async def dev_start(thread_id: str, label: str = DEFAULT_LABEL) -> dict:
    """Deterministically bring up the live preview for a thread — no LLM in the loop.

    Acquires the thread's container, finds the runnable project, installs deps if
    missing, and starts the dev server on the published preview port. The frontend
    calls this (button + auto-trigger) so the preview "just works" instead of
    nudging the agent to do it. Container/AIO sandbox only (host preview ports)."""
    label = label.replace("\n", "").replace("\r", "")
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        from deerflow.sandbox import get_sandbox_provider
        from deerflow.sandbox.dev_server import run_preview_pipeline

        provider = get_sandbox_provider()
        if not hasattr(provider, "get_preview_endpoint"):
            return {"started": False, "reason": "deterministic preview requires the container sandbox"}
        sandbox_id = await asyncio.to_thread(provider.acquire, thread_id)
        sandbox = provider.get(sandbox_id)
        if sandbox is None:
            return {"started": False, "reason": "sandbox unavailable"}
        # Fire-and-forget: install + start runs in the background; the frontend
        # polls /dev-status to see it come up.
        asyncio.create_task(run_preview_pipeline(thread_id, sandbox, label))
        return {"started": True, "label": label}
    except Exception as e:
        logger.warning("dev-start failed for thread %s: %s", thread_id.replace("\n", "").replace("\r", ""), e)
        return {"started": False, "reason": "internal error"}


@router.post("/dev-external")
async def dev_external(
    thread_id: str,
    port: int,
    host: str = "127.0.0.1",
    label: str = DEFAULT_LABEL,
) -> dict:
    """Register a dev server that is already listening on ``host:port``.

    Used when a dev server was started outside the ``start_dev_server`` /
    ``run_preview_pipeline`` path (e.g. an agent launched a Node server via a
    raw ``bash`` tool, or an operator started one manually). The panel's
    ``/dev-status`` poll picks this handle up exactly like a server the runtime
    spawned, and the Browser tab can fall back to the absproxy URL when the
    canonical preview proxy cannot reach ``host:port``.

    Refuses (HTTP 400) if the advertised host:port is not actually listening,
    so the UI never advertises a phantom server.
    """
    label = label.replace("\n", "").replace("\r", "")
    host = host.replace("\n", "").replace("\r", "")
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    if not (1 <= int(port) <= 65535):
        raise HTTPException(status_code=400, detail="port must be 1..65535")
    if not _is_port_open(host, int(port)):
        raise HTTPException(
            status_code=400,
            detail=f"port {host}:{port} is not reachable; nothing is listening there",
        )
    handle = register_external_dev_server(thread_id, int(port), host=host, label=label)
    return {
        "thread_id": handle.thread_id,
        "label": handle.label,
        "host": handle.host,
        "port": handle.port,
        "status": handle.status,
        "url": f"{_preview_prefix(thread_id, handle.label)}/",
        "absproxy_url": f"/api/sandbox/absproxy/{thread_id}/{handle.port}/",
    }


@router.get("/dev-servers")
async def dev_servers(thread_id: str) -> dict:
    """List all live dev servers for a thread (for the multi-port label dropdown)."""
    if not _caller_owns_thread(thread_id):
        return {"servers": []}
    servers = [
        {
            "label": h.label,
            "status": h.status,
            "running": h.status in ("starting", "ready"),
            "port": h.port,
            "url": f"{_preview_prefix(thread_id, h.label)}/" if h.status in ("starting", "ready") else None,
        }
        for h in list_dev_servers(thread_id)
    ]
    servers.sort(key=lambda s: (s["label"] != DEFAULT_LABEL, s["label"]))
    return {"servers": servers}


@router.get("/audit")
async def sandbox_audit(thread_id: str, download: bool = False):
    """Structured audit trail for a thread (every tool call) + JSONL export.

    The per-thread ``sandbox.log`` is already one JSON observation per line
    (ts, type, path, summary, output), so this parses it for the Audit tab and
    serves it verbatim as a downloadable ``.jsonl`` for compliance/export."""
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    user_id = get_effective_user_id()
    log_path = _sandbox_log_path(thread_id, user_id=user_id)
    raw = ""
    if log_path.exists():
        try:
            raw = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
    if download:
        return Response(
            content=raw,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="audit-{thread_id}.jsonl"'},
        )
    events: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"events": events}


@router.get("/review")
async def sandbox_review(thread_id: str, download: bool = False):
    """Deterministic dual-audience code review of a thread's workspace.

    Builds a plain-English verdict (for non-coders) + a developer section
    (per-file +/- stats, risk flags, detected checks) from git/file-scan + the
    audit trail. Writes ``REVIEW.md`` into the workspace so it's downloadable as
    an artifact. No LLM — reliable and reproducible."""
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        user_id = get_effective_user_id()
    except Exception:
        user_id = None
    from deerflow.sandbox.review import build_review

    review = await asyncio.to_thread(build_review, thread_id, user_id)
    if download:
        return Response(
            content=review.markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="REVIEW-{thread_id}.md"'},
        )
    return review.to_dict()


def sandbox_review_download_url(thread_id: str) -> str:  # pragma: no cover - helper parity
    return f"/api/sandbox/review?thread_id={thread_id}&download=true"


@router.post("/browser-check")
async def browser_check(thread_id: str, label: str = DEFAULT_LABEL, routes: str = "/"):
    """Self-test the thread's running app in the sandbox's native browser.

    Loads each route, captures console errors + render failures + a screenshot,
    and returns a structured result the Browser tab renders (and the self-improving
    loop consumes). Requires a running dev server + the container (AIO) sandbox."""
    label = label.replace("\n", "").replace("\r", "")
    routes = routes.replace("\n", "").replace("\r", "")
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        from deerflow.sandbox import get_sandbox_provider
        from deerflow.sandbox.browser_check import run_browser_check

        provider = get_sandbox_provider()
        if not hasattr(provider, "get_preview_endpoint"):
            return {"ok": False, "reason": "browser self-test requires the container sandbox", "routes": []}
        sandbox_id = await asyncio.to_thread(provider.acquire, thread_id)
        sandbox = provider.get(sandbox_id)
        if sandbox is None:
            return {"ok": False, "reason": "sandbox unavailable", "routes": []}
        route_list = [r.strip() for r in routes.split(",") if r.strip()] or ["/"]
        check = await asyncio.to_thread(run_browser_check, thread_id, sandbox, label=label, routes=route_list, with_screenshot=True)
        return check.to_dict(include_screenshot=True)
    except Exception as e:
        logger.warning("browser-check failed for thread %s: %s", thread_id.replace("\n", "").replace("\r", ""), e)
        return {"ok": False, "reason": "internal error", "routes": []}


@router.get("/browser-check-last")
async def browser_check_last(thread_id: str):
    """Return the most recent browser self-test for a thread (auto-check or manual),
    so the Browser tab can show it without re-running."""
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    from deerflow.sandbox.browser_check import get_last_browser_check

    check = get_last_browser_check(thread_id)
    if check is None:
        return {"ok": None, "reason": "no self-test yet", "routes": []}
    return check.to_dict(include_screenshot=True)


@router.post("/save-skill")
async def save_skill(thread_id: str, name: str, path: str = ""):
    """Promote a skill built in the thread workspace to the GLOBAL custom registry.

    Reads the skill dir (SKILL.md + supporting files), security-scans it, and writes
    it to ``skills/custom/<name>`` (host-mounted, survives container teardown). Custom
    skills default to enabled, so it appears in the ✨ launcher + as ``/<name>`` next chat."""
    name = name.replace("\n", "").replace("\r", "")
    path = path.replace("\n", "").replace("\r", "")
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        user_id = get_effective_user_id()
    except Exception:
        user_id = None
    from deerflow.skills.promote import promote_skill_to_global

    rel = (path or f"/mnt/user-data/workspace/{name}").replace("/mnt/user-data/workspace", "").strip("/")
    work_dir = get_paths().sandbox_work_dir(thread_id, user_id=user_id)
    source_dir = work_dir / rel if rel else work_dir
    try:
        return await promote_skill_to_global(name, source_dir, thread_id=thread_id)
    except Exception as e:
        logger.warning("save-skill failed for thread %s: %s", thread_id.replace("\n", "").replace("\r", ""), e)
        return {"saved": False, "name": name, "files": [], "reason": "internal error"}


@router.get("/terminal-url")
async def terminal_url(thread_id: str):
    """Return **same-origin** URLs for the sandbox's interactive ttyd terminal +
    noVNC browser view, for embedding in the Agent's Computer.

    These used to be absolute ``http://localhost:{published_port}`` URLs pointing
    straight at the container's published port, on the assumption that "the
    browser shares the Docker host". That only holds for local development. On
    any real deployment (nova.alilabsx.com is Docker Compose behind a Cloudflare
    tunnel; the browser is nowhere near the host) those URLs are unreachable, and
    the app shell's own CSP ``frame-src 'self' blob:`` would refuse to frame them
    anyway. Both panes were therefore permanently blank once deployed.

    Routing them through ``/api/sandbox/appview/{thread_id}/`` keeps them
    same-origin, so they satisfy ``frame-src 'self'``, inherit the proxy's opaque
    ``sandbox`` CSP, and work identically local and deployed."""
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        from urllib.parse import urlparse

        from deerflow.sandbox import get_sandbox_provider

        provider = get_sandbox_provider()
        if not hasattr(provider, "get_preview_endpoint"):
            return {"terminal": None, "vnc": None, "reason": "container sandbox required"}
        sandbox_id = await asyncio.to_thread(provider.acquire, thread_id)
        sandbox = provider.get(sandbox_id)
        base = getattr(sandbox, "base_url", None)
        if not base:
            return {"terminal": None, "vnc": None, "reason": "sandbox unavailable"}
        port = urlparse(base).port or 8080
        prefix = _appview_prefix(thread_id)
        # Keep ttyd's session_id query if the SDK provides one.
        query = ""
        try:
            client = getattr(sandbox, "_client", None)
            tu = client.shell.get_terminal_url() if client else None
            raw = str(getattr(tu, "data", tu) or "")
            query = urlparse(raw).query
        except Exception:
            query = ""
        terminal = f"{prefix}/terminal" + (f"?{query}" if query else "")
        # `port` is reported for diagnostics only — it is the container's published
        # host port and is deliberately no longer part of either URL.
        return {"terminal": terminal, "vnc": f"{prefix}/vnc/index.html", "port": port}
    except Exception as e:
        logger.warning("terminal-url failed for thread %s: %s", thread_id.replace("\n", "").replace("\r", ""), e)
        return {"terminal": None, "vnc": None, "reason": "internal error"}


# Batch 3.1: the absproxy implementation is registered as 7 separate
# routes (one per HTTP method) further down so each OpenAPI operationId
# is unique. The original single multi-method route is removed to avoid
# the Duplicate Operation ID warning. See _PROXY_METHODS below.


# Batch 3.1: OpenAPI fix — register each HTTP method as its own route so
# FastAPI generates a unique operationId per (path, method). The previous
# @router.api_route(methods=[GET, POST, ...]) form produced ONE operationId
# reused across all 7 methods, which triggered Duplicate Operation ID
# warnings during openapi spec generation (see test_openapi_operation_ids.py).
# We extract the implementation into ``_absproxy_impl`` and register each
# verb through a one-line thin wrapper.
_PROXY_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]


async def _absproxy_impl(thread_id: str, port: int, path: str, request: Request) -> Response:
    """Shared implementation for the absproxy route — called once per HTTP method."""
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        from deerflow.sandbox import get_sandbox_provider

        provider = get_sandbox_provider()
        if not hasattr(provider, "get_preview_endpoint"):
            raise HTTPException(status_code=400, detail="absproxy requires the container sandbox")
        sandbox_id = await asyncio.to_thread(provider.acquire, thread_id)
        sandbox = provider.get(sandbox_id)
        base_url = getattr(sandbox, "base_url", None)
        if not base_url:
            raise HTTPException(status_code=503, detail="sandbox unavailable")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("absproxy setup failed for thread %s: %s", thread_id.replace("\n", "").replace("\r", ""), e)
        return Response(content="absproxy error", status_code=502)

    target = f"{base_url.rstrip('/')}/absproxy/{port}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    prefix = f"/api/sandbox/absproxy/{thread_id}/{port}"
    _STRIP = _HOP_BY_HOP | {"host", "cookie", "authorization", "proxy-authorization", "x-api-key", "x-csrf-token"}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            upstream = await client.request(request.method, target, headers=fwd_headers, content=body)
    except httpx.ConnectError:
        return Response(content="<html><body style='font-family:system-ui;padding:2rem;color:#888'><h3>Nothing on that port yet</h3><p>Start the server, then retry.</p></body></html>", media_type="text/html", status_code=503)
    except Exception as e:
        logger.warning("absproxy request failed for thread %s port %d: %s", thread_id, port, e)
        return Response(content="Proxy error", status_code=502)

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    location = resp_headers.get("location") or resp_headers.get("Location")
    if location and location.startswith("/") and not location.startswith(prefix):
        resp_headers["location"] = f"{prefix}{location}"
    resp_headers.pop("set-cookie", None)
    resp_headers["Content-Security-Policy"] = _OPAQUE_SANDBOX_CSP
    return Response(content=upstream.content, status_code=upstream.status_code, headers=resp_headers, media_type=upstream.headers.get("content-type") or None)


def _appview_prefix(thread_id: str) -> str:
    """Same-origin mount point for the sandbox container's own web UI (ttyd, noVNC)."""
    return f"/api/sandbox/appview/{thread_id}"


async def _sandbox_base_url(thread_id: str) -> str:
    """Resolve the per-thread sandbox container's base URL, or raise HTTPException."""
    from deerflow.sandbox import get_sandbox_provider

    provider = get_sandbox_provider()
    if not hasattr(provider, "get_preview_endpoint"):
        raise HTTPException(status_code=400, detail="appview requires the container sandbox")
    sandbox_id = await asyncio.to_thread(provider.acquire, thread_id)
    sandbox = provider.get(sandbox_id)
    base_url = getattr(sandbox, "base_url", None)
    if not base_url:
        raise HTTPException(status_code=503, detail="sandbox unavailable")
    return str(base_url).rstrip("/")


# Injected into appview HTML so the container UI's own WebSockets come back
# through the gateway instead of straight at the app origin's root. ttyd and
# noVNC both build their socket URL in JS from `window.location`, so a <base>
# tag cannot reach them — only patching the constructor can. Cross-origin
# sockets are left untouched.
_APPVIEW_WS_SHIM = """<script>(function(){
var P=%(ws_prefix)s;var Orig=window.WebSocket;
function rw(u){try{var x=new URL(u,window.location.href);
if(x.host!==window.location.host)return u;
if(x.pathname.indexOf(P)===0)return x.href;
x.pathname=P+x.pathname;
x.protocol=(window.location.protocol==='https:')?'wss:':'ws:';
return x.href}catch(e){return u}}
function W(u,p){return p===undefined?new Orig(rw(u)):new Orig(rw(u),p)}
W.prototype=Orig.prototype;W.CONNECTING=0;W.OPEN=1;W.CLOSING=2;W.CLOSED=3;
window.WebSocket=W})();</script>"""

# Null bytes are not valid in HTML, so this cannot collide with real content.
_PREFIX_HIDDEN = "\x00prefix\x00"


def _prefix_html_urls(html: str, prefix: str) -> str:
    """Rewrite root-absolute URLs in HTML to stay under the proxy prefix.

    Idempotent: any URL that is *already* under ``prefix`` is temporarily hidden
    (``\x00`` placeholder), the naive root-absolute rewrite runs, and the hidden
    URLs are restored — so a page proxied twice, or one the browser already made
    prefix-relative, is never double-prefixed.
    """
    if not html or not prefix:
        return html
    hidden = html.replace(f"{prefix}/", _PREFIX_HIDDEN)
    rewritten = (
        hidden.replace('href="/', f'href="{prefix}/')
        .replace('src="/', f'src="{prefix}/')
        .replace('action="/', f'action="{prefix}/')
        # Next.js inlines its asset base (`"/_next/...`) without an attribute.
        .replace('"/_next/', f'"{prefix}/_next/')
    )
    return rewritten.replace(_PREFIX_HIDDEN, f"{prefix}/")


async def _proxy_appview(thread_id: str, path: str, request: Request) -> Response:
    """Proxy the sandbox container's own web UI (ttyd at /terminal, noVNC at
    /vnc/) through the gateway so it is same-origin with the app shell."""
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    base_url = await _sandbox_base_url(thread_id)

    prefix = _appview_prefix(thread_id)
    target = f"{base_url}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    _STRIP = _HOP_BY_HOP | {"host", "cookie", "authorization", "proxy-authorization", "x-api-key", "x-csrf-token"}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            upstream = await client.request(request.method, target, headers=fwd_headers, content=body)
    except httpx.ConnectError:
        return Response(content="sandbox UI unavailable", status_code=503)
    except Exception as e:
        logger.warning("appview request failed for thread %s: %s", thread_id.replace("\n", "").replace("\r", ""), e)
        return Response(content="Proxy error", status_code=502)

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    location = resp_headers.get("location") or resp_headers.get("Location")
    if location and location.startswith("/") and not location.startswith(prefix):
        resp_headers["location"] = f"{prefix}{location}"

    content = upstream.content
    content_type = upstream.headers.get("content-type", "")
    if "text/html" in content_type:
        try:
            html = content.decode("utf-8", errors="replace")
            shim = _APPVIEW_WS_SHIM % {"ws_prefix": json.dumps(f"/api/sandbox/appview-ws/{thread_id}")}
            if "<head>" in html:
                injected = f'<head><base href="{prefix}/">{shim}' if "<base " not in html else f"<head>{shim}"
                html = html.replace("<head>", injected, 1)
            else:
                html = shim + html
            html = _prefix_html_urls(html, prefix)
            content = html.encode("utf-8")
        except Exception:
            pass
        # Content-Length is now wrong for the rewritten body; httpx set it from
        # upstream. Drop it and let Starlette recompute.
        resp_headers.pop("content-length", None)
        resp_headers.pop("Content-Length", None)

    resp_headers.pop("set-cookie", None)
    resp_headers["Content-Security-Policy"] = _FRAMED_SANDBOX_CSP
    return Response(content=content, status_code=upstream.status_code, headers=resp_headers, media_type=content_type or None)


def _make_appview_wrapper(method: str):
    """Per-method wrapper around _proxy_appview (unique OpenAPI operationIds)."""

    async def handler(thread_id: str, path: str, request: Request) -> Response:
        return await _proxy_appview(thread_id, path, request)

    handler.__name__ = f"appview_{method.lower()}"
    handler.__qualname__ = handler.__name__
    handler.__doc__ = "Proxy the sandbox container's own web UI (ttyd, noVNC) same-origin through the gateway."
    return handler


for _method in _PROXY_METHODS:
    router.add_api_route(
        "/appview/{thread_id}/{path:path}",
        _make_appview_wrapper(_method),
        methods=[_method],
    )


def _make_absproxy_wrapper(method: str):
    """Build a FastAPI route handler for one HTTP method on /absproxy.

    FastAPI's default operation_id generator derives the id from
    ``route.name`` (the function name). By giving each per-method wrapper
    a unique name (``absproxy_get``, ``absproxy_post``, ...) we get
    unique operationIds per (path, method), avoiding the duplicate-id
    warning emitted by the openapi generator for multi-method routes.
    """

    async def handler(thread_id: str, port: int, path: str, request: Request) -> Response:
        return await _absproxy_impl(thread_id, port, path, request)

    handler.__name__ = f"absproxy_{method.lower()}"
    handler.__qualname__ = handler.__name__
    handler.__doc__ = "Proxy to ANY in-container port via the AIO sandbox's own /absproxy/{port}/ gateway. One route per HTTP method so each OpenAPI operationId is unique (see absproxy_<verb>)."
    return handler


for _method in _PROXY_METHODS:
    router.add_api_route(
        "/absproxy/{thread_id}/{port}/{path:path}",
        _make_absproxy_wrapper(_method),
        methods=[_method],
    )


@router.get("/dev-logs")
async def dev_logs(thread_id: str, request: Request, label: str = DEFAULT_LABEL) -> StreamingResponse:
    """SSE stream of the live dev server's stdout."""
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")

    async def gen():
        last = 0
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            handle = get_dev_server(thread_id, label)
            if handle is not None:
                lines = list(handle.log_buffer)
                if len(lines) > last:
                    for line in lines[last:]:
                        yield f"data: {line}\n\n"
                    last = len(lines)
                    idle = 0
            idle += 1
            if idle >= 30:
                yield "data: [KEEPALIVE]\n\n"
                idle = 0
            await asyncio.sleep(0.5)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
}


def _make_preview_default_wrapper(method: str):
    """Per-method wrapper around _proxy_dev_server for /preview/<id>/<path>.

    Same unique-id rationale as _make_absproxy_wrapper above.
    """

    async def handler(thread_id: str, path: str, request: Request) -> Response:
        return await _proxy_dev_server(thread_id, DEFAULT_LABEL, path, request)

    handler.__name__ = f"preview_default_label_{method.lower()}"
    handler.__qualname__ = handler.__name__
    handler.__doc__ = "HTTP-proxy requests to the thread's default (``app``) dev server."
    return handler


for _method in _PROXY_METHODS:
    router.add_api_route(
        "/preview/{thread_id}/{path:path}",
        _make_preview_default_wrapper(_method),
        methods=[_method],
    )


def _make_preview_labeled_wrapper(method: str):
    """Per-method wrapper around _proxy_dev_server for /lpreview/<id>/<label>/<path>."""

    async def handler(thread_id: str, label: str, path: str, request: Request) -> Response:
        return await _proxy_dev_server(thread_id, label, path, request)

    handler.__name__ = f"preview_labeled_{method.lower()}"
    handler.__qualname__ = handler.__name__
    handler.__doc__ = "HTTP-proxy requests to a thread's dev server with a non-default ``label``."
    return handler


for _method in _PROXY_METHODS:
    router.add_api_route(
        "/lpreview/{thread_id}/{label}/{path:path}",
        _make_preview_labeled_wrapper(_method),
        methods=[_method],
    )


async def _proxy_dev_server(thread_id: str, label: str, path: str, request: Request):
    if not _caller_owns_thread(thread_id):
        raise HTTPException(status_code=404, detail="Not found")
    _mark_sandbox_active(thread_id)
    handle = get_dev_server(thread_id, label)
    if handle is None or handle.status not in ("starting", "ready"):
        # Missing or stale handle (e.g. the registered server died but another is
        # live on a different published port, or the sandbox re-spawned): discover
        # and adopt a live server so the preview recovers instead of 503ing.
        found = await discover_live_preview(thread_id)
        if found is not None:
            container_port, host, host_port = found
            handle = adopt_handle(thread_id, label, container_port, host, host_port)
    if handle is None or handle.status not in ("starting", "ready"):
        return Response(
            content="<html><body style='font-family:system-ui;padding:2rem;color:#888'><h3>No dev server running</h3><p>Ask the agent to start the dev server.</p></body></html>",
            media_type="text/html",
            status_code=503,
        )

    # Forward the STRIPPED path (route already captures the part after the prefix)
    # to the dev server root. The dev server runs WITHOUT basePath, so this avoids
    # the trailing-slash redirect loop. We rewrite asset URLs in the HTML response
    # and any redirect Location so everything stays under the proxy prefix.
    prefix = _preview_prefix(thread_id, label)
    # Local sandbox: handle.host is 127.0.0.1. Container (AIO) sandbox: the dev
    # server runs inside the per-thread container, reached via its published
    # preview port on host.docker.internal.
    target = f"http://{handle.host}:{handle.port}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    # Never forward the parent app's auth/session to the untrusted dev server.
    _STRIP = _HOP_BY_HOP | {"host", "cookie", "authorization", "proxy-authorization", "x-api-key", "x-csrf-token"}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                target,
                headers=fwd_headers,
                content=body,
            )
    except httpx.ConnectError:
        return Response(
            content="<html><body style='font-family:system-ui;padding:2rem;color:#888'><h3>Dev server starting…</h3><p>Compiling — refresh in a few seconds.</p><script>setTimeout(()=>location.reload(),3000)</script></body></html>",
            media_type="text/html",
            status_code=503,
        )
    except Exception as e:
        logger.warning("dev-proxy request failed for thread %s: %s", thread_id, e)
        return Response(content="Proxy error", status_code=502)

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}

    # Rewrite redirect Location to stay within the proxy prefix (prevents loops).
    location = resp_headers.get("location") or resp_headers.get("Location")
    if location and location.startswith("/") and not location.startswith(prefix):
        resp_headers["location"] = f"{prefix}{location}"

    content = upstream.content
    content_type = upstream.headers.get("content-type", "")

    # Rewrite root-absolute asset URLs in HTML so the browser fetches them back
    # through this proxy (where they get stripped + forwarded again).
    if "text/html" in content_type:
        try:
            html = content.decode("utf-8", errors="replace")
            # <base> makes relative URLs resolve under the prefix
            if "<head>" in html and "<base " not in html:
                html = html.replace("<head>", f'<head><base href="{prefix}/">', 1)
            html = _prefix_html_urls(html, prefix)
            content = html.encode("utf-8")
        except Exception:
            pass

    # Defense in depth: force opaque sandbox + strip cookies so untrusted preview
    # content cannot touch the parent app's session even on direct navigation.
    # The preview is framed same-origin and must be able to authenticate, so it
    # gets the framed (non-opaque) sandbox CSP — see _FRAMED_SANDBOX_CSP.
    resp_headers.pop("set-cookie", None)
    resp_headers["Content-Security-Policy"] = _FRAMED_SANDBOX_CSP
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=content_type or None,
    )


# ──────────────────────────────────────────────────────────
# WebSocket proxy for HMR / hot reload (Next.js webpack-hmr, Vite @vite/client)
# ──────────────────────────────────────────────────────────


@router.websocket("/preview-ws/{thread_id}/{path:path}")
async def proxy_dev_server_ws(websocket: WebSocket, thread_id: str, path: str):
    """Bridge the browser's HMR WebSocket for the default (``app``) dev server."""
    await _proxy_dev_server_ws(websocket, thread_id, DEFAULT_LABEL, path)


@router.websocket("/lpreview-ws/{thread_id}/{label}/{path:path}")
async def proxy_dev_server_ws_labeled(websocket: WebSocket, thread_id: str, label: str, path: str):
    """Bridge the browser's HMR WebSocket for a labeled (multi-port) dev server."""
    await _proxy_dev_server_ws(websocket, thread_id, label, path)


@router.websocket("/appview-ws/{thread_id}/{path:path}")
async def proxy_appview_ws(websocket: WebSocket, thread_id: str, path: str):
    """Bridge ttyd's and noVNC's WebSockets to the sandbox container's own UI port.

    Reached via the WebSocket shim injected by ``_proxy_appview`` — the browser
    opens a same-origin socket here and the gateway relays it into the container,
    so the live terminal and VNC panes work on deployments where the container's
    published port is not reachable from the browser at all."""
    if not _ws_same_origin(websocket):
        await websocket.close(code=1008)
        return
    # The auth middleware never runs for WebSocket scope (BaseHTTPMiddleware is
    # skipped), so authenticate from the session cookie and stamp the contextvar
    # before the ownership check — otherwise it resolves to DEFAULT_USER_ID and
    # every real user is rejected.
    from app.gateway.ws_guards import ws_user

    user = await ws_user(websocket)
    if user is None:
        await websocket.close(code=1008)
        return
    token = set_current_user(user)
    try:
        if not _caller_owns_thread(thread_id):
            await websocket.close(code=1008)
            return
        _mark_sandbox_active(thread_id)
        base_url = await _sandbox_base_url(thread_id)

        from urllib.parse import urlsplit

        parts = urlsplit(base_url)
        query = websocket.url.query
        upstream_url = f"ws://{parts.netloc}/{path}" + (f"?{query}" if query else "")
        await websocket.accept()
        await _bridge_ws(websocket, upstream_url)
    finally:
        reset_current_user(token)


def _ws_same_origin(websocket: WebSocket) -> bool:
    """Anti cross-site-WebSocket-hijacking: only accept same-origin handshakes.

    Delegates to the shared implementation so this router and the voice router
    cannot drift apart on an admission check.
    """
    from app.gateway.ws_guards import ws_same_origin

    return ws_same_origin(websocket)


async def _bridge_ws(websocket: WebSocket, upstream_url: str) -> None:
    """Relay an already-accepted client WebSocket to ``upstream_url``.

    Handles text *and* binary frames in both directions. ttyd and noVNC are
    binary protocols, so a text-only pump silently drops their traffic.
    """
    import asyncio as _asyncio

    import websockets as _ws

    try:
        async with _ws.connect(upstream_url, open_timeout=10, max_size=None) as upstream:

            async def client_to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            return
                        if (data := msg.get("bytes")) is not None:
                            await upstream.send(data)
                        elif (text := msg.get("text")) is not None:
                            await upstream.send(text)
                except (WebSocketDisconnect, Exception):
                    return

            async def upstream_to_client():
                try:
                    async for msg in upstream:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)
                except Exception:
                    return

            await _asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception:
        pass
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


async def _proxy_dev_server_ws(websocket: WebSocket, thread_id: str, label: str, path: str):
    """Bridge the browser's HMR WebSocket to the dev server's WS so the live
    preview hot-reloads when the agent edits files."""
    if not _ws_same_origin(websocket):
        await websocket.close(code=1008)
        return

    # Authenticate (the auth middleware never runs for ws scope) and stamp the
    # contextvar so the ownership check below sees the real caller.
    from app.gateway.ws_guards import ws_user

    user = await ws_user(websocket)
    if user is None:
        await websocket.close(code=1008)
        return
    token = set_current_user(user)
    try:
        handle = get_dev_server(thread_id, label)
        if handle is None or handle.status not in ("starting", "ready"):
            await websocket.close(code=1011)
            return

        # Cross-tenant check — refuse if the caller doesn't own this thread.
        if not _caller_owns_thread(thread_id):
            await websocket.close(code=1008)
            return
        _mark_sandbox_active(thread_id)

        await websocket.accept()
        query = websocket.url.query
        upstream_url = f"ws://{handle.host}:{handle.port}/{path}" + (f"?{query}" if query else "")
        # Shared bridge: relays binary as well as text. The previous inline pump used
        # receive_text(), which raises on a binary frame and tears the socket down —
        # harmless for webpack/Vite HMR (text-only) but wrong in general.
        await _bridge_ws(websocket, upstream_url)
    finally:
        reset_current_user(token)
