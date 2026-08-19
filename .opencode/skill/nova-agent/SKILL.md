---
name: nova-agent
description: "Master skill for all Nova/DeerFlow agent work. Use this skill whenever you are building full-stack apps WITH Nova's computer agent (scaffold → verify → green loop), working ON Nova itself (middleware, harness, sprint patterns, journal hooks), or diagnosing Nova's agent-computer pipeline (white preview, 409, crash-loop, panel bugs, Stop not working). Routes to specialized references: build-loop (P1–P5 deterministic build), maintain (harness/middleware/sprint), diagnose (pipeline regressions). Triggers on: 'build me a...', 'I'm getting a 409', 'white preview', 'browser tab is blank', 'stop not working', 'add middleware', 'new sprint', 'harness boundary', 'extend Nova', 'run dev_verify', 'why is the agent stuck', 'the panel is clipped', 'BUILD_JOURNAL', 'drive to green'. Use this skill proactively — if the task touches Nova's codebase or uses Nova to build something, this skill applies."
---

# nova-agent

Read the reference file that matches your task, then follow it:

| Task | Read |
|------|------|
| Building a full-stack app with Nova's computer agent | `references/build-loop.md` |
| Improving, extending, or maintaining Nova itself | `references/maintain.md` |
| Diagnosing a pipeline regression or user-visible bug | `references/diagnose.md` |

If your task spans categories, sequence: **diagnose** (fix what's broken) → **maintain** (harness changes) → **build-loop** (validate end-to-end).

---

## Stack orientation

Nova is a DeerFlow fork — a LangGraph computer-use agent. The key layers:

```
nginx :2026
  ├── /api/langgraph/* → gateway :8001 (LangGraph runtime)
  ├── /api/*           → gateway :8001 (REST)
  └── /                → frontend :3000 (Next.js)

backend/packages/harness/deerflow/   ← agent harness (import: deerflow.*)
backend/app/gateway/                 ← FastAPI app   (import: app.*)
frontend/src/                        ← Next.js UI
```

PM2 process `deerflow` supervises the Docker Compose dev stack (`ecosystem.config.js`).  
Gateway host port: **8000** (8001 is internal; host port 8001 returns 000 — that's normal).

## Hard rules — never violate

1. **Additive/reversible**: every change revertible by one edit
2. **Local-sandbox path**: branch on `is_local_sandbox()` for provider differences; AIO gets new computer-use features, local stays byte-identical
3. **Batch backend edits**: uvicorn `--reload` recreates active sandboxes — apply when the live thread is idle
4. **Non-fatal run path**: any new code in middlewares or tools must be wrapped `try/except`; a failure must never crash a run
5. **Harness boundary**: `deerflow.*` never imports `app.*` — enforced by `tests/test_harness_boundary.py` in CI

## Health check (run this first)

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:2026/health   # expect 200
pm2 list | grep -E "^\| [0-9]+ *\| nova "   # expect: online, restarts < 10
docker ps --filter "name=deer-flow" --format "{{.Names}}\t{{.Status}}"
# expect: deer-flow-nginx Up, deer-flow-frontend Up, deer-flow-gateway Up
```

Note: the gateway does **not** publish a host port — it is reachable only from
inside the Docker network (nginx proxies to it), so `curl localhost:8001/health`
from the host will always fail and is not a signal. Go through :2026. The PM2
process is `nova` (it runs `scripts/pm2-deerflow.sh`), not `deerflow`.

**If the gateway is crash-looping, read its log BEFORE restarting anything.**
`docker logs deer-flow-gateway` is empty by design on the dev stack — the
entrypoint redirects to the host-mounted file — so an empty container log tells
you nothing:

```bash
docker ps -a --filter name=deer-flow-gateway --format '{{.Status}}'  # "Restarting (1)"?
tail -100 logs/gateway.log                                           # the actual traceback
```

Two failure modes look identical from outside (nginx logs
`gateway could not be resolved`, which just means the container is down and
Docker DNS SERVFAILed its name) but have different fixes:

| `logs/gateway.log` says | Fix |
| --- | --- |
| `FileNotFoundError: ... DEER_FLOW_CONFIG_PATH not found at /home/...` | A host path leaked in from `.env` via `env_file`. Pin the container path in the compose `environment:` block — recreating the container will NOT help. |
| `WatchfilesRustInternalError: ... Too many open files (os error 24)` | Host inotify instances exhausted. `sudo sysctl fs.inotify.max_user_instances=1024`. |
| Nothing / container never starts, and `docker ps` shows only nginx+frontend | Docker **name conflict** — see `references/diagnose.md`. This is the only one the recycle below fixes. |

Only for the name-conflict case:

```bash
pm2 stop nova
docker compose \
  -f docker/docker-compose-dev.yaml \
  -f docker/docker-compose.dood.yaml \
  -f docker/docker-compose.prod-frontend.yaml \
  -p deer-flow-dev down --remove-orphans
pm2 start nova
# Poll until 200: until curl -sf http://localhost:2026/health; do sleep 3; done
```

## Middleware chain (relevant subset, append-order)

```
ThreadDataMiddleware        — thread dirs, manifest injection
UploadsMiddleware           — newly uploaded files → state
SandboxMiddleware           — acquire sandbox, store sandbox_id
ObserveAdjustMiddleware     — task_progress, todo.md, BUILD_JOURNAL, present_files auto-verify
ReflectFixBudgetMiddleware  — dev_verify ISSUES → concrete fix directive (drive-to-green)
ViewImageMiddleware         — browser screenshot → model vision (supports_vision gate)
LoopDetectionMiddleware     — repeated tool-call detection, hard-stop injection
ClarificationMiddleware     — ask_clarification intercept (must be LAST)
```

Full chain + positions: `backend/packages/harness/deerflow/agents/middlewares/`

## Test suite commands

```bash
# Backend (from backend/):
uv run pytest tests/test_harness_boundary.py tests/test_reflect_fix_middleware.py \
  tests/test_build_journal.py tests/test_loop_detection_middleware.py -q

# Frontend (from frontend/):
pnpm lint && pnpm typecheck && pnpm build
```

Both must be green before committing anything.
