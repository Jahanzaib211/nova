"""Live dev server registry for the Agent's Computer Browser preview.

Two execution modes share one registry so the gateway can HTTP-proxy a running
dev server to the browser regardless of sandbox backend:

- **Local sandbox** (``sandbox=None``): runs ``npm run dev`` as a non-blocking
  background process on the gateway host, spawned and supervised by the
  Execution Kernel (Phase C7) so the server stays alive across agent turns
  and is reconciled at shutdown. Reached at ``127.0.0.1:{port}``.
- **AIO / container sandbox** (``sandbox=<AioSandbox>``): runs the dev server
  *inside* the per-thread container (backgrounded with ``nohup``), reached through
  the container's published preview port at ``{host.docker.internal}:{host_port}``.
  Output is tailed from a log file via ``sandbox.read_file`` (the AIO
  ``execute_command`` is blocking, so we cannot stream stdout from the host).

Servers are keyed by ``(thread_id, label)`` so a thread can run several apps
(default label ``app``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import socket
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from deerflow.execution.supervisor import SpawnedProcess

logger = logging.getLogger(__name__)

_PORT_BASE = 4100
_MAX_SERVERS = 5  # LRU cap — evict + kill oldest beyond this
_READY_MARKERS = ("ready in", "ready -", "ready on", "local:", "localhost:", "compiled", "started server")
_LOG_BUFFER_MAX = 500
DEFAULT_LABEL = "app"
# In-container preview port for the default label. Multi-port assigns extras.
DEFAULT_CONTAINER_PORT = 4100
_AIO_POLL_INTERVAL = 1.5
# Stop tailing after this many consecutive empty/error reads (~_AIO_POLL_INTERVAL
# each) so a dead/never-started dev server can't spin the poller forever.
_MAX_AIO_MISSES = 60
# Read short, well-known constant names for the new "crashed" terminal state.
_STATUS_CRASHED = "crashed"
_READINESS_TIMEOUT_S = 12.0  # panel flips starting → ready/crashed inside this window


@dataclass
class DevServerHandle:
    thread_id: str
    port: int
    cwd: str
    command: str
    label: str = DEFAULT_LABEL
    host: str = "127.0.0.1"  # gateway-reachable host (AIO: host.docker.internal)
    container_port: int = DEFAULT_CONTAINER_PORT  # in-container port (AIO only)
    process: SpawnedProcess | None = None  # local mode only (kernel-supervised)
    log_buffer: deque[str] = field(default_factory=lambda: deque(maxlen=_LOG_BUFFER_MAX))
    status: str = "starting"  # starting | ready | error | stopped | crashed
    compiles: int = 0  # increments on each recompile → frontend auto-reloads
    _order: int = 0
    # AIO-mode bookkeeping (None in local mode).
    _sandbox: object | None = None
    _logpath: str | None = None
    _poller_task: asyncio.Task | None = None
    _watchdog_task: asyncio.Task | None = None  # port-readiness watchdog (local + AIO)


_servers: dict[str, DevServerHandle] = {}
_order_counter = 0


def _server_key(thread_id: str, label: str = DEFAULT_LABEL) -> str:
    return f"{thread_id}::{label}"


def _ensure_host_binding(command: str) -> str:
    """Make a dev command bind all interfaces so the container's published port
    can reach it (frameworks default to localhost, which the host port can't hit).

    Next.js reads ``HOSTNAME`` env (handled by the launch env), so no flag is
    needed for it. Vite ignores env host, so append ``--host 0.0.0.0`` — either
    directly (``vite``) or via the package-manager passthrough (``-- --host``).
    Commands that already specify a host are left untouched.
    """
    low = command.lower()
    if "--host" in low or " -h " in low or "--hostname" in low:
        return command
    if "vite" in low:
        if any(low.startswith(pm) or f" {pm} " in f" {low}" for pm in ("npm run", "pnpm", "yarn", "bun")):
            return command + " -- --host 0.0.0.0"
        return command + " --host 0.0.0.0"
    return command


def get_dev_server(thread_id: str, label: str = DEFAULT_LABEL) -> DevServerHandle | None:
    return _servers.get(_server_key(thread_id, label))


async def port_alive(host: str, port: int, timeout: float = 0.4) -> bool:
    """Fast TCP liveness check for a dev server.

    This is the authority for 'running': it stays true as long as the server
    actually listens, regardless of whether the log-tail poller is still alive
    (fixes 'preview showed once then went blank' when the sandbox client resets
    between turns).
    """
    if not host or not port:
        return False
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True
    except Exception:
        return False


def list_dev_servers(thread_id: str) -> list[DevServerHandle]:
    """All dev servers for a thread (used by the multi-port label dropdown)."""
    prefix = f"{thread_id}::"
    return [h for k, h in _servers.items() if k.startswith(prefix)]


# In-container preview ports published per sandbox (mirrors AioSandboxProvider's
# DEFAULT_PREVIEW_PORTS). The default label always takes the first.
_PREVIEW_CONTAINER_PORTS = (4100, 4101, 4102)


def allocate_container_port(thread_id: str, label: str = DEFAULT_LABEL) -> int:
    """Pick an in-container preview port for a (thread, label) under AIO.

    The default ``app`` label always uses the first published port; additional
    labels take the next published port not already in use by another live
    server for this thread (so multi-port apps don't collide)."""
    if label == DEFAULT_LABEL:
        return DEFAULT_CONTAINER_PORT
    used = {h.container_port for h in list_dev_servers(thread_id) if h.label != label}
    for port in _PREVIEW_CONTAINER_PORTS:
        if port not in used:
            return port
    return _PREVIEW_CONTAINER_PORTS[-1]


async def discover_live_preview(thread_id: str) -> tuple[int, str, int] | None:
    """Find a live HTTP server among the thread's published preview ports.

    Used when the registered handle is missing or stale but a dev server is
    actually listening (e.g. started outside the pipeline, or the handle went
    dead after a sandbox re-spawn). Probes each published preview port in order
    and returns ``(container_port, host, host_port)`` for the first hit, else
    ``None``. Liveness is the same TCP authority the status endpoint uses.
    """
    from deerflow.sandbox import get_sandbox_provider

    provider = get_sandbox_provider()
    getter = getattr(provider, "get_preview_endpoint", None)
    if getter is None:
        return None
    for container_port in _PREVIEW_CONTAINER_PORTS:
        try:
            endpoint = getter(thread_id, container_port)
        except Exception:
            continue
        if not endpoint:
            continue
        host, host_port = endpoint
        if await port_alive(host, host_port):
            return (container_port, host, host_port)
    return None


def register_external_dev_server(
    thread_id: str,
    port: int,
    *,
    host: str = "127.0.0.1",
    label: str = DEFAULT_LABEL,
) -> DevServerHandle:
    """Register a dev server that is already listening on ``host:port``.

    Used by ``POST /api/sandbox/dev-external`` when a dev server was started
    outside the ``start_dev_server`` / ``run_preview_pipeline`` path (e.g. a
    raw ``bash`` tool launched a Node server, or an operator started one
    manually). The handle is marked ``ready`` immediately; no readiness
    watchdog is needed because the caller proved the port is listening at
    registration time. Idempotent: replacing a live handle with a new
    registration is allowed.
    """
    global _order_counter
    _order_counter += 1
    handle = DevServerHandle(
        thread_id=thread_id,
        port=port,
        cwd="",
        command="(external)",
        label=label,
        host=host,
        _order=_order_counter,
    )
    handle.status = "ready"
    handle.log_buffer.append(f"[deerflow] registered external dev server at {host}:{port}")
    _servers[_server_key(thread_id, label)] = handle
    return handle


def adopt_handle(
    thread_id: str,
    label: str,
    container_port: int,
    host: str,
    host_port: int,
) -> DevServerHandle:
    """Register (or refresh) a ``ready`` handle pointing at a live server.

    Used by the preview proxy / status endpoints to rescue a preview when the
    registered handle is missing or dead but a server is actually listening on a
    published port. Idempotent: if a live handle already exists for the key it is
    returned unchanged.
    """
    existing = _servers.get(_server_key(thread_id, label))
    if existing is not None and existing.status in ("starting", "ready"):
        return existing
    handle = DevServerHandle(
        thread_id=thread_id,
        port=host_port,
        cwd="",
        command="",
        label=label,
        host=host,
        container_port=container_port,
        status="ready",
        _order=_order_counter,
    )
    handle.log_buffer.append(f"[deerflow] adopted live dev server on {host}:{host_port}")
    _servers[_server_key(thread_id, label)] = handle
    return handle


def has_live_dev_server(thread_id: str) -> bool:
    """True if the thread has a live dev-server handle on a listening port.

    Used by the sandbox idle reaper to keep an in-use sandbox alive: the preview
    proxy never calls ``provider.get()``, so preview traffic does not refresh the
    idle timer and an actively-watched preview would otherwise be reaped every
    ``idle_timeout``. Sync and thread-safe (blocking socket probe), so it can be
    called from the reaper's plain thread.
    """
    for handle in list_dev_servers(thread_id):
        if handle.status not in ("starting", "ready"):
            continue
        if handle.status == "starting":
            return True  # mid-boot — don't reap while it may be coming up
        if not handle.port:
            continue
        try:
            with socket.create_connection((handle.host, handle.port), timeout=0.4):
                return True
        except Exception:
            continue
    return False


async def run_preview_pipeline(thread_id: str, sandbox: object, label: str = DEFAULT_LABEL) -> dict:
    """Deterministically bring up a live preview for a thread's project (AIO).

    No LLM in the loop: find the runnable project (nearest package.json), run
    `npm install` if needed, then start the dev server on the published preview
    port. Idempotent — if a server for (thread,label) is already registered it is
    left as-is. Runs inside the per-thread container via ``sandbox.execute_command``.
    """
    ws = "/mnt/user-data/workspace"
    existing = get_dev_server(thread_id, label)
    if existing is not None and existing.status in ("starting", "ready"):
        return {"started": True, "label": label, "already": True}

    def _exec(cmd: str) -> str:
        try:
            return sandbox.execute_command(cmd) or ""
        except Exception as e:  # pragma: no cover - defensive
            return f"Error: {e}"

    # 1) Locate the project (nearest package.json, skipping node_modules).
    found = await asyncio.to_thread(
        _exec,
        f"find {ws} -maxdepth 3 -name package.json -not -path '*/node_modules/*' 2>/dev/null | head -1",
    )
    pkg = next((ln.strip() for ln in (found or "").splitlines() if ln.strip().endswith("package.json")), "")
    if not pkg:
        _append_devlog_to_sandbox_log(thread_id, "[preview] no runnable project (package.json) found")
        return {"started": False, "reason": "no_project"}
    projdir = pkg.rsplit("/", 1)[0] or ws

    # 2) Install deps only if missing (deterministic, idempotent).
    need = await asyncio.to_thread(_exec, f"[ -d {shlex.quote(projdir)}/node_modules ] && echo yes || echo no")
    if "yes" not in (need or ""):
        _append_devlog_to_sandbox_log(thread_id, f"[preview] installing dependencies in {projdir} …")
        out = await asyncio.to_thread(_exec, f"cd {shlex.quote(projdir)} && npm install 2>&1 | tail -6")
        for ln in (out or "").splitlines():
            if ln.strip():
                _append_devlog_to_sandbox_log(thread_id, "[preview] " + ln[:200])

    # 3) Start the dev server on the published preview port.
    container_port = allocate_container_port(thread_id, label)
    # Deterministic port/process hygiene: free the target port before binding so a
    # stray/zombie server (e.g. a forgotten `python -m http.server`) can't keep the
    # port and serve stale content. Runs every time — no reliance on the model
    # remembering to clean up. Only touches the exact port we're about to use.
    await asyncio.to_thread(
        _exec,
        f"fuser -k {container_port}/tcp 2>/dev/null || kill $(lsof -ti tcp:{container_port}) 2>/dev/null || true",
    )
    handle = await start_dev_server(thread_id, projdir, "npm run dev", sandbox=sandbox, label=label, container_port=container_port)

    # 4) Auto verify-on-preview (deterministic self-improving loop): once the
    # server is up, run a browser self-test in the background so EVERY preview is
    # checked without anyone asking. Result is stored + logged for the panel/agent.
    if handle.status != "error":
        asyncio.create_task(_auto_verify_preview(thread_id, sandbox, label))
    return {"started": handle.status != "error", "label": label, "status": handle.status, "project": projdir}


async def _auto_verify_preview(thread_id: str, sandbox: object, label: str) -> None:
    """Wait for the dev server to become ready, then run one browser self-test."""
    try:
        for _ in range(40):  # up to ~40s for first compile
            await asyncio.sleep(1.0)
            h = get_dev_server(thread_id, label)
            if h is None or h.status == "error":
                return
            if h.status == "ready":
                break
        from deerflow.sandbox.browser_check import run_browser_check

        check = await asyncio.to_thread(run_browser_check, thread_id, sandbox, label=label, routes=["/"], with_screenshot=True)
        verdict = "✓ passed" if check.ok else "✗ found issues"
        _append_devlog_to_sandbox_log(thread_id, f"[self-test] browser check {verdict}")
        for r in check.routes:
            for ce in r.console_errors[:3]:
                _append_devlog_to_sandbox_log(thread_id, f"[self-test] console: {ce[:160]}")
    except Exception as e:  # pragma: no cover - best effort
        logger.debug("auto-verify preview failed for %s: %s", thread_id.replace("\n", "").replace("\r", ""), e)


def _allocate_port() -> int:
    used = {h.port for h in _servers.values()}
    port = _PORT_BASE
    while port in used:
        port += 1
    return port


def _append_devlog_to_sandbox_log(thread_id: str, text: str) -> None:
    """Mirror a dev-server output line into the per-thread sandbox.log so the
    frontend Terminal (which tails sandbox.log via SSE) shows live dev output."""
    try:
        import datetime as _dt
        import json as _json

        from deerflow.config.paths import get_paths
        from deerflow.runtime.user_context import get_effective_user_id

        try:
            user_id = get_effective_user_id()
        except Exception:
            user_id = None
        thread_dir = get_paths().thread_dir(thread_id, user_id=user_id)
        thread_dir.mkdir(parents=True, exist_ok=True)
        entry = _json.dumps(
            {
                "ts": _dt.datetime.now().strftime("%H:%M:%S"),
                "type": "bash",
                "path": None,
                "summary": "[dev] " + text[:200],
                "output": "",
            }
        )
        with open(thread_dir / "sandbox.log", "a", encoding="utf-8") as fh:
            fh.write(entry + "\n")
    except Exception:
        pass


def _ingest_line(handle: DevServerHandle, text: str) -> None:
    """Common per-line handling for both local and AIO output streams."""
    handle.log_buffer.append(text)
    if text.strip():
        _append_devlog_to_sandbox_log(handle.thread_id, text)
    low = text.lower()
    if handle.status == "starting" and any(m in low for m in _READY_MARKERS):
        handle.status = "ready"
        logger.info("Dev server for thread %s (%s) is ready on %s:%s", handle.thread_id, handle.label, handle.host, handle.port)
    # Count recompiles so the frontend can auto-reload the preview iframe.
    if "compiled" in low or "hmr" in low or "hot updated" in low:
        handle.compiles += 1


async def _watch_dev_server_start(handle: DevServerHandle, timeout: float = _READINESS_TIMEOUT_S) -> None:
    """Flip the handle to ``ready`` once the port is live, or to ``crashed`` if the
    dev server never binds before *timeout* seconds.

    Without this the panel can sit on ``status: "starting"`` forever when the
    child process dies immediately (typo'd command, missing binary, container-side
    exec that never wrote the log file). The 12 s grace is shorter than the 40 s
    ``_auto_verify_preview`` wait so the user sees the failure first.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            # Bail out if anything else already settled the handle (pump_output on
            # early exit, /stop, the readiness-marker scan).
            if handle.status in ("ready", "error", "stopped", _STATUS_CRASHED):
                return
            if handle.host and handle.port and await port_alive(handle.host, handle.port):
                if handle.status == "starting":
                    handle.status = "ready"
                    logger.info(
                        "Dev server for thread %s (%s) bound %s:%s within readiness window",
                        handle.thread_id,
                        handle.label,
                        handle.host,
                        handle.port,
                    )
                return
            if loop.time() >= deadline:
                if handle.status == "starting":
                    handle.status = _STATUS_CRASHED
                    msg = f"dev server crashed: did not bind {handle.host}:{handle.port} within {timeout:g}s"
                    handle.log_buffer.append(f"[deerflow] {msg}")
                    _append_devlog_to_sandbox_log(handle.thread_id, msg)
                    logger.warning(msg)
                return
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        raise
    except Exception:  # pragma: no cover - defensive
        logger.debug("dev server readiness watchdog ended", exc_info=True)


async def _pump_output(handle: DevServerHandle) -> None:
    """Read the (local) process stdout line-by-line into the ring buffer."""
    proc = handle.process
    if proc is None or proc.stdout is None:
        return
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            _ingest_line(handle, text)
    except Exception:
        logger.debug("dev server output pump ended", exc_info=True)
    finally:
        # Process exited
        if handle.status not in ("stopped", "ready"):
            handle.status = "error"
        elif handle.status == "ready" and proc.returncode is not None:
            handle.status = "stopped"


async def _pump_output_aio(handle: DevServerHandle) -> None:
    """Tail the in-container dev-server log via ``sandbox.read_file`` (poll).

    The AIO ``execute_command`` is blocking, so we cannot stream stdout from the
    host. Instead the dev server redirects output to a log file inside the
    container which we poll and diff for new lines.
    """
    sandbox = handle._sandbox
    logpath = handle._logpath
    if sandbox is None or logpath is None:
        return
    seen = 0
    misses = 0
    try:
        while handle.status != "stopped":
            await asyncio.sleep(_AIO_POLL_INTERVAL)
            if handle.status == "stopped":
                break
            # Only the registered handle's poller should run; if this handle was
            # replaced/removed (new dev server for the same key), stop tailing.
            if _servers.get(_server_key(handle.thread_id, handle.label)) is not handle:
                break
            try:
                content = await asyncio.to_thread(sandbox.read_file, logpath)
            except Exception:
                content = ""
            # A released sandbox can never serve another read, so stop the LOOP
            # rather than spin forever and saturate the thread pool. Asked
            # directly via `sandbox.closed`; this used to string-match the SDK's
            # AttributeError text ("has no attribute"), so rewording that
            # exception would have silently resurrected the spin.
            #
            # Do NOT drop the handle or mark it stopped: the
            # dev server itself is almost certainly still listening on its port, and
            # liveness is judged by a real TCP check in /dev-status. Killing the
            # handle here is what caused "preview showed once then went blank".
            if sandbox.closed:
                handle.log_buffer.append("[deerflow] preview log tail stopped (sandbox client reset); server still served by port check")
                break
            # read_file returns "Error: ..." until the file exists; treat as empty.
            if not content or content.startswith("Error:"):
                misses += 1
                # The log never appeared after a long while — stop tailing. Only
                # mark error if we never even reached "ready"; a ready server keeps
                # its handle so the preview persists.
                if misses >= _MAX_AIO_MISSES:
                    if handle.status == "starting":
                        handle.status = "error"
                        handle.log_buffer.append("[deerflow] dev server produced no output; stopping log tail")
                    break
                continue
            misses = 0
            if len(content) <= seen:
                continue
            fresh = content[seen:]
            seen = len(content)
            for line in fresh.splitlines():
                _ingest_line(handle, line.rstrip("\n"))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("AIO dev server log poller ended", exc_info=True)


async def _evict_if_needed() -> None:
    if len(_servers) < _MAX_SERVERS:
        return
    # kill the oldest
    oldest = min(_servers.values(), key=lambda h: h._order)
    await stop_dev_server(oldest.thread_id, oldest.label)


async def start_dev_server(
    thread_id: str,
    cwd: str,
    command: str = "npm run dev",
    *,
    sandbox: object | None = None,
    label: str = DEFAULT_LABEL,
    container_port: int = DEFAULT_CONTAINER_PORT,
) -> DevServerHandle:
    """Start (or restart) a dev server for *thread_id* in directory *cwd*.

    When *sandbox* is provided (AIO/container mode) the dev server runs inside
    the container and is reached through its published preview port; otherwise
    it runs as a host subprocess (local mode).
    """
    global _order_counter

    # If one is already running for this (thread, label), stop it first
    existing = _servers.get(_server_key(thread_id, label))
    if existing is not None:
        await stop_dev_server(thread_id, label)

    await _evict_if_needed()

    _order_counter += 1

    if sandbox is not None:
        return await _start_dev_server_aio(thread_id, cwd, command, sandbox, label, container_port)

    return await _start_dev_server_local(thread_id, cwd, command, label)


async def _start_dev_server_local(thread_id: str, cwd: str, command: str, label: str) -> DevServerHandle:
    port = _allocate_port()
    handle = DevServerHandle(
        thread_id=thread_id,
        port=port,
        cwd=cwd,
        command=command,
        label=label,
        host="127.0.0.1",
        _order=_order_counter,
    )

    # SECURITY: do NOT inherit the gateway's full environment — it holds backend
    # API keys / secrets that must never reach agent-controlled subprocess code.
    # Allowlist only what a dev server legitimately needs.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/root"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PORT": str(port),
        "HOSTNAME": "127.0.0.1",
        "NODE_ENV": "development",
        "BROWSER": "none",  # don't try to open a browser
        "CI": "1",  # disable interactive prompts
    }

    handle.log_buffer.append(f"$ PORT={port} {command}")
    handle.log_buffer.append(f"[deerflow] starting dev server on port {port} in {cwd}")

    try:
        from deerflow.execution import ExecutionClass, ExecutionRequest, ResourceLimits
        from deerflow.services.container import service_container

        kernel = service_container.execution_kernel()
        proc = await kernel.spawn(
            ExecutionRequest(
                argv=("/bin/sh", "-c", command),
                execution_class=ExecutionClass.SHELL,
                cwd=cwd,
                env=env,
                limits=ResourceLimits(grace_period=5.0),
                intent=f"dev server {label} for thread {thread_id}",
                thread_id=thread_id,
            ),
            start_new_session=True,  # detach so it survives the tool call
        )
    except Exception as e:
        handle.status = "error"
        handle.log_buffer.append(f"[deerflow] failed to start: {e}")
        _servers[_server_key(thread_id, label)] = handle
        return handle

    handle.process = proc
    _servers[_server_key(thread_id, label)] = handle
    asyncio.create_task(_pump_output(handle))
    handle._watchdog_task = asyncio.create_task(_watch_dev_server_start(handle))
    return handle


async def _start_dev_server_aio(
    thread_id: str,
    cwd: str,
    command: str,
    sandbox: object,
    label: str,
    container_port: int,
) -> DevServerHandle:
    """Start the dev server inside the per-thread container, reached via the
    container's published preview port."""
    from deerflow.sandbox import get_sandbox_provider

    handle = DevServerHandle(
        thread_id=thread_id,
        port=0,
        cwd=cwd,
        command=command,
        label=label,
        container_port=container_port,
        _order=_order_counter,
        _sandbox=sandbox,
    )

    endpoint = None
    try:
        provider = get_sandbox_provider()
        getter = getattr(provider, "get_preview_endpoint", None)
        if getter is not None:
            endpoint = getter(thread_id, container_port)
    except Exception as e:
        logger.warning("Failed to resolve preview endpoint for thread %s: %s", thread_id.replace("\n", "").replace("\r", ""), e)

    if endpoint is None:
        handle.status = "error"
        handle.log_buffer.append(f"[deerflow] no published preview port {container_port} for this thread's sandbox")
        _servers[_server_key(thread_id, label)] = handle
        return handle

    handle.host, handle.port = endpoint
    logpath = f"/mnt/user-data/workspace/.deerflow-dev-{container_port}.log"
    handle._logpath = logpath

    # The log/pid files must exist BEFORE the child runs so the SSE tail at
    # /api/sandbox/logs always has something to read — even when the child
    # crashes immediately and never writes a single line.
    #
    # This used to be `Path(logpath).touch()` right here, which never once
    # worked under the AIO provider: `logpath` is a path *inside the sandbox
    # container* (/mnt/user-data/workspace/...), while this code runs in the
    # gateway, where /mnt is empty. Every call raised FileNotFoundError into the
    # debug-level except below, so the window this guard exists to close stayed
    # open for every dev server — and the resulting 404-per-poll was logged at
    # ERROR by `read_file` about once a second. The touch is now part of the
    # in-container command instead, where the path actually resolves.
    # Background the dev server inside the container, binding 0.0.0.0 so the
    # published port reaches it (HOSTNAME covers Next.js; the host flag covers
    # Vite). Launch under `setsid` so the server is its own process-group leader:
    # the recorded PID == PGID, letting stop kill the WHOLE tree (npm wrapper +
    # next-server child). Killing only the npm wrapper leaves a zombie next-server
    # holding the port and serving a stale webpack chunk map (the 500 chunk bug).
    bound_command = _ensure_host_binding(command)
    # Apply env via `env` AFTER setsid (not a shell-assignment prefix before it):
    # `setsid env VAR=val cmd` makes setsid exec `env`, which sets the vars and
    # execs the dev command. This is robust even if the command retains a stray
    # leading assignment, avoiding `setsid: failed to execute PORT=…`.
    inner = (
        f"touch {shlex.quote(logpath)} {shlex.quote(logpath + '.pid')}; "
        f"cd {shlex.quote(cwd)} && "
        f"setsid env PORT={container_port} HOST=0.0.0.0 HOSTNAME=0.0.0.0 BROWSER=none CI=1 "
        f"{bound_command} > {shlex.quote(logpath)} 2>&1 < /dev/null & "
        f"echo $! > {shlex.quote(logpath + '.pid')}; echo deerflow-dev-started"
    )

    handle.log_buffer.append(f"$ PORT={container_port} {command}")
    handle.log_buffer.append(f"[deerflow] starting dev server in container at {cwd} (preview {handle.host}:{handle.port})")

    try:
        await asyncio.to_thread(sandbox.execute_command, inner)
    except Exception as e:
        handle.status = "error"
        handle.log_buffer.append(f"[deerflow] failed to start in container: {e}")
        _servers[_server_key(thread_id, label)] = handle
        return handle

    _servers[_server_key(thread_id, label)] = handle
    handle._poller_task = asyncio.create_task(_pump_output_aio(handle))
    handle._watchdog_task = asyncio.create_task(_watch_dev_server_start(handle))
    return handle


async def stop_dev_server(thread_id: str, label: str = DEFAULT_LABEL) -> bool:
    handle = _servers.pop(_server_key(thread_id, label), None)
    if handle is None:
        return False
    handle.status = "stopped"

    # Cancel the AIO log poller if any.
    poller = handle._poller_task
    if poller is not None and not poller.done():
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            _ = await poller

    # Cancel the readiness watchdog (only fires while status == "starting").
    watchdog = handle._watchdog_task
    if watchdog is not None and not watchdog.done():
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            _ = await watchdog

    # AIO mode: kill the WHOLE process group + free the port. The recorded PID is
    # the setsid group leader, so `kill -- -<pid>` (negative = process group)
    # reaps the npm wrapper AND the next-server/vite child. A port-targeted
    # fallback (fuser/lsof) catches anything that re-parented or detached, so no
    # zombie keeps holding the port or serving a stale chunk map.
    if handle._sandbox is not None and handle._logpath is not None:
        pidfile = handle._logpath + ".pid"
        port = handle.container_port
        kill_cmd = (
            f"pid=$(cat {shlex.quote(pidfile)} 2>/dev/null); "
            f'if [ -n "$pid" ]; then kill -TERM -"$pid" 2>/dev/null; sleep 1; kill -KILL -"$pid" 2>/dev/null; fi; '
            f"fuser -k {port}/tcp 2>/dev/null || kill $(lsof -ti tcp:{port} 2>/dev/null) 2>/dev/null; "
            f"rm -f {shlex.quote(pidfile)}"
        )
        with contextlib.suppress(Exception):
            await asyncio.to_thread(handle._sandbox.execute_command, kill_cmd)
        return True

    # Local mode: terminate the kernel-supervised host process (TERM →
    # grace → KILL handled by the supervisor).
    proc = handle.process
    if proc is not None and proc.returncode is None:
        with contextlib.suppress(Exception):
            await proc.terminate_gracefully(5)
    return True
