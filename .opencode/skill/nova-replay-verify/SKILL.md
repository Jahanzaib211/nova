---
name: nova-replay-verify
description: Verify a Nova change against the existing replay E2E contract. Use when changing any code that touches the agent loop, middlewares, tools, gateway routes, or sandbox behavior. Re-runs `backend/tests/test_replay_golden.py` (Layer 1, no API key) and the fullstack Playwright render (Layer 2, no API key). If replay misses, the fixture is stale and the skill walks through re-recording with `backend/scripts/record_gateway.py`. Triggers on phrases like "verify this", "is this safe", "does it break replay", or before merging any PR touching `backend/app/gateway/**`, `backend/packages/harness/**`, or `frontend/**`.
---

# nova-replay-verify

## When to use

ALWAYS run this skill before declaring any change to Nova's agent loop, gateway routes, or middlewares safe. The replay contract is the cheapest deterministic check we have; if it passes, you have provable evidence the SSE shape and the full-stack render are intact.

## Layer 1: backend golden (no API key, ~3s)

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_replay_golden.py -v
```

Expected: `1 passed, 0 failed` (or more if a new fixture was added).

If the run fails with `replay miss (N): the fixture is stale vs the current system prompt or agent graph`, the agent graph has drifted. Follow the **re-recording** workflow below.

## Layer 2: full-stack render (no API key, ~3min)

```bash
cd frontend
pnpm exec playwright test -c playwright.real-backend.config.ts
```

Expected: 3 specs pass (auth-disabled-contract, multi-run-order, real-backend-render).

If this fails, the frontend SSE consumer or browser render path has drifted. Check the failure log for the specific selector or assertion.

## Re-recording (when a fixture is stale)

Use ONLY when you've intentionally changed the agent graph, prompt, or tool list:

```bash
# 1. Configure env (needs real model credentials)
export OPENAI_API_KEY=sk-...
export DEERFLOW_RECORD_OUT=/tmp/nova-capture.jsonl

# 2. Drive a real session through the recording gateway
cd backend
uv run python -m backend.scripts.record_gateway.py

# 3. Convert the JSONL capture to a fixture
uv run python -m backend.scripts.build_fixture_from_jsonl.py \
    /tmp/nova-capture.jsonl \
    backend/tests/fixtures/replay/<your-fixture-name>.json

# 4. Regenerate the golden
DEERFLOW_WRITE_GOLDEN=1 uv run pytest tests/test_replay_golden.py -v

# 5. Commit BOTH files (.json and .events.json) in the same PR
```

## When NOT to use this skill

- Changes to copy/text (rebrand) → use `pnpm format` + the lint check.
- Changes to docs → just visual review.
- Changes to tests themselves → the test is the contract, no re-record needed.

## Failure-class triage

- `KeyError: 'tools'` / `'hooks'` / `'subagents'` → capabilities endpoint mismatch. Read `backend/app/gateway/routers/capabilities.py:_safe_*` to confirm shape.
- `assert 503 == 200` → `config.yaml` not loaded in test. Patch with the `_app_config` MagicMock fixture pattern from `test_capabilities_endpoint.py:_hermetic_config_fixture`.
- `RuntimeError: There is no current event loop` → `asyncio.run()` not `asyncio.get_event_loop()`. Patch.
- `browserType.launch: Failed to launch chromium` → `playwright install chromium --with-deps` not run, OR `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` hardcoded to a version that doesn't exist.
