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
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health   # expect 200
pm2 list | grep deerflow   # expect: online, restarts < 10
docker ps --filter "name=deer-flow" --format "{{.Names}}\t{{.Status}}"
# expect: deer-flow-nginx Up, deer-flow-frontend Up, deer-flow-gateway Up
```

If gateway is crash-looping (restarts > 20 in pm2, or only nginx+frontend in `docker ps`):

```bash
pm2 stop deerflow
docker compose \
  -f docker/docker-compose-dev.yaml \
  -f docker/docker-compose.dood.yaml \
  -p deer-flow-dev down --remove-orphans
pm2 start deerflow
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
