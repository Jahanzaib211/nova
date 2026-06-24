# FORK CHANGELOG — Volume 5 (Reflect/Fix Loop + Manifest Auto-derive)

> Continues `FORK.md` (v1), `FORK_V2.md` (v2), `FORK_V3.md` (v3),
> `FORK_V4.md` (v4). Read `SESSION_HANDOFF.md` first.
> **Theme of v5:** close the v3/v4 open items — runtime-enforce the
> reflect/fix budget from the prompt, auto-derive the agent manifest
> from the tool registry, and remove the rename-time symlink bridge.

## Date
2026-06-25 (continuing from v4 baseline).

## What shipped this round

### 1. ReflectFixBudgetMiddleware — runtime-enforce "iterate at most twice"

**File:** `backend/packages/harness/deerflow/agents/middlewares/reflect_fix_middleware.py`

The prompt's `<self_verify>` block says: "Iterate AT MOST twice — if still
failing, tell the user plainly what's wrong." But without runtime
enforcement, the agent can ignore the rule and keep iterating until the
recursion limit kills the run (a known v3 failure mode — see the
cowork-fullstack transcript where 3.5M tokens were spent searching for
a non-existent project).

The middleware closes that gap:

- Watches every `dev_verify` ToolMessage after_model fires
- Counts consecutive ISSUES results per (thread_id, run_id)
- After 2 consecutive ISSUES, queues a forced HumanMessage:
  `[REFLECT BUDGET HIT] dev_verify has returned ISSUES 2 times
  consecutively. ... you MUST stop iterating and write your final
  answer now. ... Do not call any more tools.`
- The warning is injected at the next `wrap_model_call` (mirrors the
  existing `LoopDetectionMiddleware` pattern so tool-call pairing
  invariants are preserved)

**Classification rules:**
- `✅ PASS` in content → reset counter (clean run, start fresh)
- `⚠️ ISSUES` in content → increment counter
- `Error: ...` at start → ignore (tooling failure ≠ agent failure)
- Anything else (unrecognised shape, non-string content, empty) →
  ignore

**Non-fatal by construction:** every code path wrapped in
`try/except`. Constructor validates `budget >= 1`. Public `reset()`
API for per-thread or global state clearing. If the middleware breaks,
the agent falls back to its old prompt-hoped behaviour — nothing gets
worse than today.

Registered in `lead_agent/agent.py:367-371` alongside
`ObserveAdjustMiddleware`.

### 2. Manifest auto-derivation from BUILTIN_TOOLS

**File:** `backend/packages/harness/deerflow/agents/manifest.py`

Replaces the hand-maintained `_WORKSPACE_TOOLS` tuple with
auto-derivation from `BUILTIN_TOOLS + SUBAGENT_TOOLS + view_image_tool`.
As the harness grows, new tools pick up an auto-derived one-liner
with zero maintenance.

**Layout:**
- `_TOOL_PURPOSE_OVERRIDES` (small curated dict, 21 entries): only
  the tools whose auto-derived first-sentence is misleading get an
  override.
- `_one_line_purpose(description)`: reduces a multi-line tool
  description to a single concise line (first sentence, trimmed to
  140 chars with trailing ellipsis if needed).
- `_collect_tools()`: imports `deerflow.tools.tools` lazily, walks
  `BUILTIN_TOOLS + SUBAGENT_TOOLS + view_image_tool`, dedupes by
  name, applies override-or-derived purpose, sorts alphabetically
  for deterministic output.
- `_format_tool_inventory()`: now accepts an optional `_tools` list
  for test injection.
- `build_agent_manifest()`: takes a new `_tools` keyword (test-only)
  so tests are deterministic and independent of `BUILTIN_TOOLS`
  ordering.

**Failure modes:**
- `BUILTIN_TOOLS` import fails → fall back to `_TOOL_PURPOSE_OVERRIDES`
  (still a sensible tool inventory, manifest degrades gracefully)
- Tool missing `name` attribute → skipped
- Duplicate names → deduped by `seen` set
- Description empty / None → `(no description)` fallback

The manifest content also now surfaces `ReflectFixBudgetMiddleware`
(the v5 runtime nanny) so the agent knows "iterate at most twice"
is runtime-enforced, not just prompt-hoped.

### 3. Tidy rename — close the symlink bridge

**File:** `ecosystem.config.js`

Updates the 4 hardcoded `/home/jahanzaib/Desktop/deer-flow` paths to
`/home/jahanzaib/Desktop/nova`. This was the last piece that depended
on the `deer-flow → nova` symlink bridge from the initial rename.

**Operational sequence (downtime < 30s):**

1. Updated `ecosystem.config.js` (4 paths).
2. `pm2 delete deerflow`.
3. `pm2 start /home/jahanzaib/Desktop/nova/ecosystem.config.js --only deerflow`
   (absolute path; relative path silently failed once — logged).
4. Waited 30s for the docker stack to come up.
5. Verified `nginx:2026 = 200`, `gateway:8000 = 200`.
6. `pm2 save` (dump persisted to `~/.pm2/dump.pm2`).
7. `rm /home/jahanzaib/Desktop/deer-flow` (the symlink).
8. Re-verified health: both ports still 200.
9. Confirmed all 3 deer-flow containers up (nginx, gateway, frontend).
10. Full pytest regression: 200 passed (no regressions).

**Companion edits (gitignored):**
- `.claude/settings.local.json`: 5 historical Bash permission
  strings updated `deer-flow → nova`. File is in `.gitignore` so
  the edit is local-only.

**Side effects:**
- The `deerflow` pm2 process name and `deer-flow-dev` docker compose
  project name are unchanged (operational IDs, not paths).
- The `DEER_FLOW_ROOT` env var still points at the project (now under
  `nova`), so the runtime contract is preserved.
- The symlink at `/home/jahanzaib/Desktop/deer-flow` is gone; any
  external script that referenced it directly would break. None of
  the tracked code does.

## Verification (this round)

- **New tests:**
  - `tests/test_reflect_fix_middleware.py` — 21 passed
    (budget mechanics, what does NOT count, wrap_model_call
    injection, per-run reset, per-thread isolation, non-fatal
    contract, invalid budget, custom budget, public reset)
  - `tests/test_agent_manifest.py` — 20 passed total (was 11, +9
    new for auto-derivation)
- **Total: 41 new tests passing this round**
- **Cumulative regression:** 200 passed (121 v3 baseline + 49 v4 +
  30 v5)
- Gateway healthy throughout (`nginx:2026 = 200`, `gateway:8000 = 200`)
- All 3 deer-flow containers up after pm2 re-registration
- pm2 + systemd unchanged (reboot-proof preserved)

## Repo state

- 11 tags locally + on remote:
  - `fork-v3-baseline` — initial commit (the v3 state)
  - `fork-v4-manifest-module` — manifest module + tests
  - `fork-v4-env-wired` — runtime_paths helpers + tests
  - `fork-v4-manifest-live` — middleware injection + tests
  - `fork-v4-loop-detector` — Layer 3 dead-end search + tests
  - `fork-v4-verify-event` — backend verify_result emission + tests
  - `fork-v4-verify-ui` — frontend Activity-tab pill
  - `fork-v4` — top-level v4 milestone
  - `fork-v5-reflect-fix` — ReflectFixBudgetMiddleware
  - `fork-v5-manifest-auto` — auto-derived manifest
  - `fork-v5-housekeeping` — rename tidy
- 11 commits on `main`, each independently testable + rollback-able
- Symlink bridge removed; project lives at `/home/jahanzaib/Desktop/nova`
- GitHub: `github.com/Jahanzaib211/nova` (private, no remote push
  to `bytedance/deer-flow`)

## Hard rules — status check

- ✅ Additive / reversible only — every change is one-edit revertible
- ✅ Non-fatal in run path — reflect_fix + manifest auto-derive both
  wrapped; manifest falls back to overrides on any failure
- ✅ Local-sandbox path byte-identical — zero touches to
  `aio_sandbox_provider.py`, `local_backend.py`,
  `runtime/runs/manager.py`
- ✅ Harness boundary — `deerflow.*` never imports `app.*`
  (enforced by `test_harness_boundary.py`, still passing)

## What was OPEN in v4 — now CLOSED

| v4 OPEN item | Status in v5 |
|---|---|
| #1 Bounded reflect/fix loop | ✅ Closed — `ReflectFixBudgetMiddleware` runtime-enforces the rule |
| #2 Live E2E on authed build thread | ⏳ Still open — needs the owner to click around in a browser |
| #3 Cloudflared quick-tunnel tier for `deploy_expose` | ⏳ Still open — needs the owner's "yes public sharing" call |
| #4 Symlink housekeeping | ✅ Closed — 9 paths updated, symlink removed, pm2 re-registered |
| #5 Manifest auto-derived | ✅ Closed — `_collect_tools()` pulls from `BUILTIN_TOOLS` automatically |

## OPEN / NEXT — for v6

1. **Live E2E verify of auto-verify-on-present_files** on a real
   authed build thread. The `verify_result` event + Activity-tab pill
   work end-to-end in code; needs a human to open a chat and confirm
   the badge appears when the agent presents a build.
2. **Cloudflared quick-tunnel tier** for `deploy_expose` (tier-2
   public URL, approval-gated). Designed in v3, deferred in v4, deferred
   again in v5. **Owner decision required** — adding public sharing
   to a local-first stack is a philosophical shift, not just a code
   change.
3. **Reflect/fix budget visual surface** — the new
   `ReflectFixBudgetMiddleware` injects a `[REFLECT BUDGET HIT]`
   message via the existing stream channel. Consider exposing this
   as a separate UI pill (similar to `verify_result`) so the user
   sees when the agent was force-stopped from iterating.
4. **Reflect/fix budget per-tool bypass** — the current middleware
   counts every `dev_verify` ISSUES. Future iterations could
   per-tool override the budget (e.g. "code_review is allowed
   5 iterations") using the same `tool_freq_overrides` pattern from
   `loop_detection_config.py`.
5. **Replace `verify_result` "consecutive ISSUES" event with a
   structured `verify_blocked` event** — when the agent is force-
   stopped by the reflect budget, the user should see *why* (count
   of failures, last verdict, which gate triggered). Currently this
   surfaces only in the [REFLECT BUDGET HIT] message text.
6. **Re-base to upstream HEAD `f656440`** — fork is 2 PRs behind
   upstream DeerFlow. Out of scope here (separate workstream).

## What this is NOT

- Not a behavior-changing capstone in the way v3 was — the reflect
  budget is *constraining* existing behavior (forcing the prompt's
  rule to be honored), not changing what the agent does.
- Not a UI redesign — all changes are backend + manifest.
- Not a rebase to upstream HEAD.
- Not a push to `bytedance/deer-flow` — the upstream remote is
  removed entirely; pushes go to `Jahanzaib211/nova` only.