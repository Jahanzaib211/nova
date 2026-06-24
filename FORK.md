# DeerFlow — Fork Changes (Agent's Computer + Per-Thread Containers)

This document describes what this fork adds on top of **stock DeerFlow**. Upstream docs
(`README.md`, `backend/CLAUDE.md`, `frontend/CLAUDE.md`) still describe the base system; this file
covers only the deltas.

The headline: stock DeerFlow runs the agent and serves text/artifacts; this fork turns it into a
**Manus / z.ai–style build environment** — a 3-panel "Agent's Computer", **one isolated Docker
container per conversation**, and a **live, streaming preview** of the app the agent is building.

---

## 1. Agent's Computer (new 3-panel UI)

A right-hand panel that shows what the agent is actually doing, in real time.

- **Terminal** — live stream of the agent's bash/npm output (SSE).
- **Editor** — live view of the file the agent is currently writing.
- **Browser** — the running app, rendered live in an iframe (desktop/mobile toggle, reload,
  open-in-new-tab); falls back to an HTML-blob preview for single-page `index.html`, and a
  "Start Live Preview" placeholder for code projects with no server yet.
- **Activity** — structured tool-call cards + a nested workspace file tree.
- **Task progress** — a checklist driven by the agent's `todo.md`.

New frontend (untracked dirs are entirely new):
- `frontend/src/components/workspace/agent-computer/` — the panel (`agent-computer-panel.tsx`).
- `frontend/src/components/workspace/panels/` — panel layout/context.
- `frontend/src/core/sandbox/` — hooks: `useSandboxLogs`, `useSandboxFiles`, `useSandboxFile`,
  `useLiveFileContent`, `useSandboxTodo`, `useDevServerStatus(threadId, label)`, `useDevServers`.
- Wired into the chat via `components/workspace/chats/chat-box.tsx`,
  `messages/context.ts`, and the `app/workspace/.../chats/...` routes; i18n keys added in
  `core/i18n/locales/*`.

---

## 2. Per-thread isolation: AIO sandbox is the default

Stock DeerFlow defaults to `LocalSandboxProvider` (everything runs in the gateway process). This fork
runs **`AioSandboxProvider`** — **each thread gets its own Docker container** (`deer-flow-sandbox-*`)
via the host Docker daemon (Docker-out-of-Docker). Benefits: true bash/file isolation, persistent
state across turns, and no shared-process build races.

- `config.yaml` → `sandbox.use: deerflow.community.aio_sandbox:AioSandboxProvider` (with a commented
  `LocalSandboxProvider` block for one-line rollback).
- The gateway needs the host Docker socket — mounted via `docker/docker-compose.dood.yaml`.
- Reachability: containers publish on the host; the gateway reaches them via
  `DEER_FLOW_SANDBOX_HOST` (`host.docker.internal` in Docker).

Changed: `community/aio_sandbox/{aio_sandbox_provider,local_backend,sandbox_info}.py`.

---

## 3. Live dev-server preview (the core feature)

When the agent runs `npm run dev` (or `vite`, `next start`, `serve`, `python -m http.server`), the
running app is streamed into the **Browser** tab automatically — no manual step.

How it works:
- **Auto-detection**: `sandbox/tools.py::_looks_like_dev_server` recognizes dev/prod/static servers
  and routes them to a non-blocking registry instead of a blocking `bash` call.
- **In-container launch** (`sandbox/dev_server.py`): the server runs inside the thread's container
  under `setsid env PORT=… HOST=0.0.0.0 …` (its own process group). Each container publishes preview
  ports **4100–4102**; the gateway resolves the published host port via
  `AioSandboxProvider.get_preview_endpoint`.
- **Reverse proxy** (`app/gateway/routers/sandbox.py`): `/api/sandbox/preview/{thread_id}/…` proxies
  to the container, rewriting HTML asset URLs / redirects to stay under the prefix, stripping the
  parent app's auth, and forcing a CSP sandbox. WebSocket HMR is proxied too.
- **Live updates**: the panel polls `/dev-status` and auto-reloads the iframe on each recompile.
- **Reliable teardown**: `stop_dev_server` kills the whole process group + `fuser -k {port}` — no
  zombie `next-server` left holding the port or serving a stale webpack chunk map.
- **Multi-port apps**: start several servers with `start_dev_server(label="api")`; the Browser tab
  shows a **label dropdown** when more than one is running. Routes:
  `/lpreview/{thread_id}/{label}/…` + `GET /dev-servers`.

Endpoints added in `routers/sandbox.py`: `/logs`, `/todo`, `/status`, `/file`, `/files`,
`/download-zip`, `/dev-status`, `/dev-servers`, `/dev-logs`, `/preview/{thread_id}/{path}`,
`/lpreview/{thread_id}/{label}/{path}`, `/preview-ws/…`, `/lpreview-ws/…`.

---

## 4. Backend agent/runtime additions

- **Observe + Adjust middleware** (`agents/middlewares/observe_adjust_middleware.py`, new) — emits
  `task_progress` / `task_activity` and maintains the per-thread `todo.md` that drives the panel's
  task checklist.
- **Sandbox observation logging** (`sandbox/tools.py`) — every tool call (bash/write/str_replace/
  read/ls/search/grep/scaffold/dev-server) is written to a per-thread `sandbox.log` (works for both
  local **and** AIO sandboxes), which feeds the Terminal + Activity panels.
- **Workspace tools** (`tools/builtins/workspace_tools.py`, new) — `search_files`, `grep_files`,
  `scaffold_project` (Next.js/Vite/HTML templates that bind `0.0.0.0`), `start_dev_server`,
  `stop_dev_server`.
- **Harness security checks** (`sandbox_audit_middleware.py`) — advisory secret-scan on `write_file`
  and slopsquatting/typosquat check on `npm/pnpm/yarn install` (non-blocking notes).
- **Anti-thrash preview prompt** (`agents/lead_agent/prompt.py`) — `<live_preview>` rules: start the
  dev server once, never probe ports, never build while dev is running, prefer `npm run dev` for the
  live stream.
- **Auth rate limiting** (`app/gateway/auth_rate_limit_middleware.py`, new) — per-IP sliding window on
  login/register/change-password (429 + Retry-After); registered in `app/gateway/app.py`.
- **Stuck-run recovery** (`runtime/runs/manager.py`) — `create_or_reject` reaps inflight runs whose
  asyncio task is already finished, so a dead run can't wedge a thread behind a permanent HTTP 409.
- **GitHub token injection** — `AioSandboxProvider` injects host `$GITHUB_TOKEN` into each container
  only when set (never a raw `$VAR` in `config.yaml`, which would break config loading).

---

## 5. Running it (PM2 + Docker, AIO mode)

DeerFlow here is supervised by **PM2**, which runs the Docker dev stack with the DooD overlay:

```js
// ecosystem.config.js  (app "deerflow")
script: "/usr/bin/docker", interpreter: "none",
args: "compose -f docker/docker-compose-dev.yaml -f docker/docker-compose.dood.yaml \
       -p deer-flow-dev up --no-build --scale provisioner=0"
```

- Start / re-register: `pm2 start ecosystem.config.js --only deerflow && pm2 save`
- App: `http://localhost:2026` (nginx → gateway/frontend). Gateway runs `uvicorn --reload`, frontend
  runs `next dev` — both volume-mount source, so code edits hot-reload without a rebuild.
- The `docker-compose.dood.yaml` overlay mounts `/var/run/docker.sock` so AIO can create per-thread
  containers. `pm2 save` persists this so AIO survives a reboot.

**Rollback to stock behavior:** in `config.yaml` comment the AIO `sandbox:` block, uncomment the
`LocalSandboxProvider` block, then restart the stack. All new code paths branch on
`is_local_sandbox`, so the local path is unchanged.

---

## 6. File map (what changed vs upstream)

New files:
- `backend/app/gateway/routers/sandbox.py` — sandbox + live-preview API.
- `backend/packages/harness/deerflow/sandbox/dev_server.py` — dev-server registry/launch/proxy glue.
- `backend/packages/harness/deerflow/tools/builtins/workspace_tools.py` — workspace + dev-server tools.
- `backend/packages/harness/deerflow/agents/middlewares/observe_adjust_middleware.py` — progress/todo.
- `backend/app/gateway/auth_rate_limit_middleware.py` — auth rate limiting.
- `backend/tests/test_dev_server_preview.py`, additions to `tests/test_run_manager.py` — coverage.
- `frontend/src/components/workspace/agent-computer/`, `.../panels/`, `frontend/src/core/sandbox/`.
- `ecosystem.config.js`, `docker/docker-compose.dood.yaml`.

Modified files (high level): `config.yaml` (AIO default), `.dockerignore` (exclude `.deer-flow`),
`backend/app/gateway/{app.py,routers/__init__.py}`, `community/aio_sandbox/*`, `runtime/runs/manager.py`,
`sandbox/tools.py`, `agents/lead_agent/{agent.py,prompt.py}`, `agents/middlewares/sandbox_audit_middleware.py`,
`tools/tools.py`, and the frontend chat/route/i18n files that mount the Agent's Computer.

---

## 7. Test coverage for fork features
- `backend/tests/test_dev_server_preview.py` — preview-port publishing, `get_preview_endpoint`,
  in-container launch (`setsid env`), process-group kill shape, env-prefix stripping, dev-server
  command recognition, multi-port label keying, container-port allocation, AIO observation routing.
- `backend/tests/test_run_manager.py` — stale-run reaping (409 recovery).

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_dev_server_preview.py tests/test_run_manager.py -q`
