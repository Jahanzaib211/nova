# FORK CHANGELOG — Volume 3 (Deterministic computer-use)

> Continues `FORK.md` (v1) and `FORK_V2.md` (v2). Read `SESSION_HANDOFF.md` first.
> Theme of v3: make the computer-use layer **deterministic** — verification happens by
> runtime rule, not by hoping the model remembers. Every change is additive, reversible,
> and **non-fatal** (a failure can never break a run).

## Date
2026-06-24.

## What shipped this round

### 1. Auto-verify-on-`present_files` (the deterministic headline)
**File:** `backend/packages/harness/deerflow/agents/middlewares/observe_adjust_middleware.py`

The agent already auto-verified **running** previews (`dev_server.py::_auto_verify_preview`).
The gap was the **static deliverable** path: when the agent ships a self-contained HTML build via
`present_files`, nothing checked that it actually rendered. Now it does — deterministically.

- New `ObserveAdjustMiddleware._maybe_verify_present_files()` runs inside the existing (already
  non-fatal) `aafter_tool` hook. When the latest tool result is a successful `present_files`, it fires
  a **background** `run_browser_check` against the just-presented HTML (auto-targeted by
  `browser_check` when no dev server is up).
- **No model choice.** It's a runtime gate — every presented build is self-tested, mirroring
  auto-verify-on-preview. This is the "deterministic top-1% dev" completion the owner asked for.
- **Guards:** fires once per distinct `(thread_id, sorted(artifacts))` set (module-level
  `_verified_present` dedup, re-fires when the deliverable set changes); skips local sandboxes
  (browser_check needs the AIO chromium); resolves `thread_id` from `config.configurable`; resolves
  the live sandbox via `get_sandbox_provider().get(sandbox_id)`.
- **Surface:** results are appended as `[self-test] …` lines to the per-thread `sandbox.log`, which the
  Terminal tab already tails (a proven channel). **Deliberately no new custom SSE event type** — to
  avoid any frontend parse coupling while the owner is away.
- **Non-fatal:** the whole path is wrapped; a failure is logged at debug and swallowed.

**Tests:** `backend/tests/test_observe_adjust_present_verify.py` — fires once, dedups, re-fires on new
artifacts, skips local sandbox, skips non-present tools. 4 passed.

### 2. Playwright persisted (the agent's eyes survive a clean rebuild)
**Files:** `backend/packages/harness/pyproject.toml`, `backend/uv.lock`

`browser_check` drives Playwright over the AIO chromium's CDP. Playwright was only in the
host-mounted `.venv` (survives restarts/reboots, **not** a from-scratch image rebuild). Now declared as
`playwright>=1.40.0` in the harness deps and pinned in `uv.lock` (resolved to 1.60.0). The running
`.venv` was **not** re-synced — zero disruption to the live stack; a clean rebuild now keeps the eyes.

## Verification (this round)
- Imports clean against the real container venv (observe_adjust, dev_server devlog helper, browser_check,
  playwright 1.60.0).
- `tests/test_harness_boundary.py tests/test_loop_detection_middleware.py tests/test_run_manager.py` →
  **121 passed**. New gate tests → **4 passed**.
- Gateway healthy throughout (`localhost:2026/health` and `:8000/health` → 200).
- uvicorn `--reload` picks up the middleware change automatically (no restart needed).

## Carried-forward deterministic layer (from v1/v2, still in force)
- Tools: `system_probe`, `free_port`, `dev_verify` (tests→browser_check→code_review battery),
  `code_review`, `browser_check`, `save_skill`, AIO-native nodes (PTY shell_*, browser_*, deploy_expose,
  agent_notify).
- Gates: port hygiene before every preview; auto-verify-on-preview; loop-budget reset per run +
  raised build-tool caps.
- `browser_check` via Playwright-over-CDP; blank judged by screenshot pixels (canvas/JS safe).
- Prompt: `<command_arsenal>`, `<enterprise_capabilities>`, `<self_verify>` (mandates `dev_verify`).

## Not done (intentionally deferred — see SESSION_HANDOFF.md "OPEN / NEXT")
- Frontend Activity-tab styling for verify results (already visible in Terminal via `[self-test]`).
- cloudflared quick-tunnel tier for `deploy_expose` (tier-2 public URL).
- Bounded reflect/fix loop consuming `dev_verify` ISSUES (capstone; behavior-changing — design carefully).
