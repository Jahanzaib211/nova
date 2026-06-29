# nova-agent: Maintain Nova

How to safely extend, improve, or refactor Nova's codebase. Every change must survive a reload — no run-path crashes, no harness boundary violations, no silent breaks in CI.

## Sprint pattern

The standard workflow for any non-trivial change:

```
1. Audit   — read the code that actually exists (code > docs > memory)
2. Plan    — write to ~/.claude/plans/<slug>.md before implementing
3. Pillar  — one logical change per commit; each pillar is independent
4. Test    — uv run pytest (backend) + pnpm lint && pnpm typecheck && pnpm build (frontend)
5. Commit  — feat/fix/chore(scope): description
6. Verify  — health check + pm2 restarts < 5
```

Keep changes additive and reversible. Never group backend + frontend in one untested commit.

## Adding a new middleware

1. Create `backend/packages/harness/deerflow/agents/middlewares/my_middleware.py`
2. Subclass `AgentMiddleware` from `langchain.agents.middleware`
3. Override only the hooks you need (wrap them `try/except` — non-fatal)
4. Wire into the chain in `build_lead_runtime_middlewares()` or `build_middlewares()` in `agent.py`

The chain is **append-order**. ClarificationMiddleware must always be last.

Available hooks (most common):
- `before_agent(state, runtime)` — once per run, before graph entry
- `aafter_tool(state, config)` — after every tool result (async)
- `wrap_model_call(request, handler)` → inject into model context
- `awrap_model_call(request, handler)` → async version

Non-fatal pattern:
```python
async def aafter_tool(self, state, config):
    try:
        # your logic
        pass
    except Exception:
        logger.debug("MyMiddleware: skipped", exc_info=True)
    return state
```

## Wiring journal hooks

Use `record_middleware` to emit a structured audit event. Only call this for meaningful state changes — not pure-observation middleware.

**From a model-call hook** (`wrap_model_call` / `awrap_model_call`):
```python
runtime = getattr(request, "runtime", None)
ctx = getattr(runtime, "context", None)
journal = ctx.get("__run_journal") if isinstance(ctx, dict) else None
if journal is not None:
    journal.record_middleware(
        "my_tag",
        name="MyMiddleware",
        hook="wrap_model_call",
        action="inject_something",
        changes={"key": "value"},
    )
```

**From a tool-lifecycle hook** (`aafter_tool` / `before_agent`) via config:
```python
runtime = (config.get("configurable") or {}).get("__pregel_runtime") if isinstance(config, dict) else None
ctx = getattr(runtime, "context", None)
journal = ctx.get("__run_journal") if isinstance(ctx, dict) else None
```

Always wrap in `try/except`. Journal is `None` in unit tests and subagent runs — that's expected.

Existing pattern reference: `skill_activation_middleware.py:202-220`, `reflect_fix_middleware.py:_inject_into_request`.

## Harness boundary rule

`deerflow.*` never imports `app.*`. This is enforced in CI by `tests/test_harness_boundary.py`.

```python
# ALLOWED: app imports harness
from deerflow.config import get_app_config

# FORBIDDEN: harness imports app
from app.gateway.routers.uploads import ...  # will fail CI
```

If you need gateway behavior from the harness, put the shared logic in the harness and have gateway call it.

## Editing the system prompt

The prompt lives in `backend/packages/harness/deerflow/agents/lead_agent/prompt.py` as a Python function returning a `ChatPromptTemplate`. Read `nova-prompt-author` skill before touching it.

Critical: **never use `{variable}` brace syntax in raw prompt strings** — the template is compiled with `.format()` and any `{...}` will try to substitute. Use `{{literal braces}}` if you need them in the output, or restructure to avoid them.

Test after every prompt change:
```bash
cd backend && uv run python -c "
from deerflow.agents.lead_agent.prompt import apply_prompt_template
print('prompt compiles OK')
"
```

## BUILD_JOURNAL naming

The generic per-thread build journal writes to `workspace/BUILD_JOURNAL.md` — **not** `CHANGELOG.md`. This was renamed to avoid clobbering any project's own changelog file. The agent-internal convention is: `todo.md`, `REVIEW.md`, `BUILD_JOURNAL.md` (all caps, agent-scoped names that won't conflict with project files).

If you see code writing to `CHANGELOG.md` from within middleware, that's a bug — rename it to `BUILD_JOURNAL.md`.

## Backend test structure

```
backend/tests/
  test_harness_boundary.py    ← CI: no app.* imports from harness
  test_reflect_fix_middleware.py
  test_build_journal.py
  test_loop_detection_middleware.py
  test_run_manager.py
  test_worker_*.py
  blocking_io/               ← Blockbuster runtime gate (CI-enforced, hard fail)
```

Run before every commit:
```bash
cd backend && uv run pytest tests/test_harness_boundary.py -q  # always
uv run pytest tests/                                           # full suite
```

## Frontend code quality

Before committing any frontend change:
```bash
cd frontend
pnpm lint        # must be 0 errors (warnings OK if pre-existing)
pnpm typecheck   # tsc --noEmit, must be clean
pnpm build       # next build, must succeed
```

Never:
- `console.log` in `frontend/src/`
- `as any` or `@ts-ignore` in changed files
- Hardcoded strings in UI — use `t.common.X` i18n keys (add to `types.ts`, `en-US.ts`, `zh-CN.ts`)

## Safe commit template

```
feat(middleware): short description of what was added

Why: one sentence on the problem this solves.
How: one sentence on the approach.

Tests: uv run pytest tests/test_X.py -q (N passed)
Frontend: pnpm lint + typecheck + build green
```

## Batch backend changes

uvicorn runs with `--reload`. Every file save triggers a reload which **recreates all active AIO sandboxes** in the current thread. Apply backend changes only when the live thread is idle, and group related changes into one batch (read all files, make all edits, verify once).
