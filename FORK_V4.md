# FORK CHANGELOG — Volume 4 (Bootstrap seam + verification surface)

> Continues `FORK.md` (v1), `FORK_V2.md` (v2), `FORK_V3.md` (v3).
> Read `SESSION_HANDOFF.md` first.
> **Theme of v4:** close the bootstrap gap — give the agent canonical
> self-knowledge on turn 1, surface the deterministic verification
> results to the user, and catch the dead-end-search failure mode at
> the detector layer.

## Date
2026-06-24.

## Naming
The local repo at `/home/jahanzaib/Desktop/nova` is named after the
"new star" — a single word that fuses the Manus-killer ambition with
the panel's defining trait (one view → six facets). Pushed to a new
private GitHub repo `Jahanzaib211/nova` (no upstream DeerFlow lineage;
the upstream `bytedance/deer-flow` remote is removed entirely).

## What shipped this round

### 1. AGENT_MANIFEST — spawn-time self-knowledge primer
**File:** `backend/packages/harness/deerflow/agents/manifest.py`

The agent already auto-verified running previews (v3) and the static
deliverable path had a deterministic check (v3). But the *bootstrap*
gap remained: agents had to infer their own capabilities from the
prompt's tool menu. The pasted-transcript failure (the agent that
hallucinated `cowork-fullstack` and exhausted 3.5M tokens searching
for it) was a symptom.

The fix is structural: a deterministic, fire-once-per-thread
self-knowledge block in the system message. The agent sees it on
turn 1 without having to read or remember anything.

**`build_agent_manifest(runtime) -> str`** returns a markdown block
listing:

- Project root (resolved via `runtime_paths.project_root()`)
- Sandbox capabilities (AIO Docker, PTY, browser-over-CDP)
- The full 26-tool inventory (every tool registered in the harness)
- Verification gates (port hygiene, auto-verify-on-preview,
  auto-verify-on-present_files, [self-test] sandbox.log lines)
- Hard rules (additive/reversible, non-fatal, harness boundary,
  local-sandbox byte-identical)
- Agent's Computer panel tabs (Files, Terminal, Editor, Browser,
  Activity, Review) + ttyd / VNC embeds
- Search discipline rule (call `ask_clarification` before searching
  the filesystem exhaustively — anchors the new dead-end-search
  loop detector category)

Properties:
- **Deterministic** — same output for the same runtime state
- **I/O-free** on the success path (no open/read/requests calls)
- **Falls back to cwd** if `project_root()` raises
- **Wrapped in try/except** at the injection point so a failure
  never breaks the run

### 2. Manifest injection at run start
**File:** `backend/packages/harness/deerflow/agents/middlewares/thread_data_middleware.py`

`ThreadDataMiddleware.before_agent` now prepends the manifest as a
SystemMessage on the first run of each thread. The injection is:

- **Fire-once-per-thread** — detects an existing `<agent_manifest>`
  tag in any SystemMessage and skips re-injection
- **Non-fatal** — if `build_agent_manifest()` raises, logs at debug
  and returns messages unchanged
- **Preserves all existing behaviour** — thread_data paths still
  returned, HumanMessage run_id/timestamp stamping still applied
- **Smaller blast radius** than touching `agent.py` (the middleware
  is the right architectural seam)

### 3. `set_project_root_from_cwd` + `in_container` helpers
**File:** `backend/packages/harness/deerflow/config/runtime_paths.py`

Two small additive helpers:

- `set_project_root_from_cwd(overwrite=False)` — idempotent helper
  that promotes `Path.cwd()` to `DEER_FLOW_PROJECT_ROOT`. Useful
  for ad-hoc scripts and test harnesses.
- `in_container()` — best-effort container detection (checks
  `/proc/1/cgroup` markers + `/.dockerenv` sentinel). Used by the
  manifest to decide whether to surface container-specific notes.

The existing `project_root()` behaviour is unchanged.

### 4. Loop detector — Layer 3: dead-end search divergence
**File:** `backend/packages/harness/deerflow/agents/middlewares/loop_detection_middleware.py`

Adds a third detection layer that catches the failure mode where
the model invents a project name (e.g. `cowork-fullstack`) and
exhaustively searches the filesystem for it with N distinct
commands that all return ENOENT.

Why this layer exists:
- Layer 1 (hash-based) catches identical tool call sets → different
  commands produce different hashes, miss.
- Layer 2 (frequency-based) catches the same tool type called many
  times → different commands are different tool calls, miss.
- **Layer 3** inspects ToolMessage *content* for ENOENT patterns
  and tracks distinct basenames per thread.

Detection logic:
- Walk back through up to 20 ToolMessages (no AI-message boundary,
  so multi-turn patterns aggregate)
- Extract basenames via regex (quoted path, verb+path, or fallback
  to first path-like token), normalize by stripping punctuation
  and reducing to last path component
- Pick the most-frequent basename in the scan; increment the
  per-thread counter once per call (one call = one agent step)
- At 5 distinct calls → forced-clarification warning
- At 8 distinct calls → hard stop + forced final answer

Per-run reset parallels `_tool_freq` so long-lived threads don't
accumulate false positives across runs.

### 5. `verify_result` custom event + Activity-tab pill
**Files:**
- `backend/packages/harness/deerflow/agents/middlewares/observe_adjust_middleware.py`
- `frontend/src/core/threads/hooks.ts`
- `frontend/src/components/workspace/messages/context.ts`
- `frontend/src/app/workspace/chats/[thread_id]/page.tsx`
- `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`
- `frontend/src/components/workspace/chats/chat-box.tsx`
- `frontend/src/components/workspace/agent-computer/agent-computer-panel.tsx`

The Terminal tab has always tailed `[self-test]` sandbox.log lines.
v4 adds a structured `verify_result` custom event alongside the log
channel — additive, the log stays the source of truth.

Event payload:
```ts
{
  type: 'verify_result',
  thread_id: string,
  ok: boolean,
  verdict: 'passed' | 'issues',
  routes: Array<{ route, ok, status, notes }>,
  console_errors_count: number,
  screenshot: string | null,
}
```

Frontend surface: a compact pill at the top of the Activity tab in
the Agent's Computer panel. Emerald tone for `ok`, amber for
`issues`. Includes route count + console error count.

Non-fatal contract preserved end-to-end:
- Backend emission wrapped in try/except
- Unknown event shapes don't break the stream parser
- Existing task_progress / task_activity paths unchanged

## Verification (this round)

- New tests:
  - `test_agent_manifest.py` — 11 passed (manifest shape, determinism,
    non-fatal fallback, I/O-free on success path)
  - `test_runtime_paths_env.py` — 8 passed (env wiring, idempotent
    helpers, in_container detection)
  - `test_manifest_injection.py` — 6 passed (fire-once-per-thread,
    non-fatal on build failure, HumanMessage stamping preserved)
  - `test_loop_detector_dead_end.py` — 15 passed (Layer 3 detection,
    per-thread isolation, per-run reset, threshold / hard-stop
    semantics)
  - `test_verify_result_event.py` — 9 passed (payload shape,
    JSON-serialisable, non-fatal emission)
- Total: **49 new tests passing**
- Harness boundary + loop + run-manager regression: **161 passed**
  (121 baseline + 40 new)
- Frontend: `pnpm tsc --noEmit` clean; `pnpm lint` clean (no new
  errors vs baseline)
- Gateway healthy throughout (`:2026/health` and `:8000/health`
  → 200)

## Repo state

- GitHub: `github.com/Jahanzaib211/nova` (private)
- 8 tags locally + on remote:
  - `fork-v3-baseline` — initial commit (the v3 state)
  - `fork-v4-manifest-module` — manifest module + tests
  - `fork-v4-env-wired` — runtime_paths helpers + tests
  - `fork-v4-manifest-live` — middleware injection + tests
  - `fork-v4-loop-detector` — Layer 3 dead-end search + tests
  - `fork-v4-verify-event` — backend verify_result emission + tests
  - `fork-v4-verify-ui` — frontend Activity-tab pill
  - `fork-v4` (top-level)
- 8 commits on `main`, each independently testable + rollback-able
- Local `.venv` untouched (host-mounted, Playwright already present)
- pm2 + systemd unchanged (reboot-proof preserved)

## Hard rules — status check

- ✅ Additive / reversible only — every change is one-edit revertible
- ✅ Non-fatal in run path — manifest injection, verify_result emission,
  loop detector all wrapped
- ✅ Local-sandbox path byte-identical — no changes to `is_local_sandbox`
  branching or the AIO / local provider code
- ✅ Harness boundary — `deerflow.*` never imports `app.*`
  (enforced by `test_harness_boundary.py`, still passing)

## OPEN / NEXT — for v5

1. **Bounded reflect/fix loop** (the v3 capstone). Feed `dev_verify`
   ISSUES back as a structured event the lead consumes and re-runs
   until green or budget hit. Deterministic stop. Behavior-changing —
   design with care, keep non-fatal. **Owner should greenlight in
   person.**
2. **Live E2E verify of auto-verify-on-present_files** on a real
   authed build thread (could not drive an authed chat from CLI in
   v3 or v4). Confirm the `verify_result` custom event renders in
   the Activity panel and `[self-test]` lines appear in the Terminal.
3. **Cloudflared quick-tunnel tier** for `deploy_expose` (tier-2
   public URL, approval-gated) — designed in v3, not built.
4. **Symlink housekeeping** — the hardcoded
   `/home/jahanzaib/Desktop/deer-flow` paths in
   `ecosystem.config.js` and `.claude/settings.local.json` still
   resolve via the symlink bridge. A separate housekeeping commit
   can update them to `nova` and remove the symlink. **Optional.**
5. **Manifest content review** — as the harness grows, the manifest
   inventory should be auto-derived from `BUILTIN_TOOLS` rather than
   a hand-maintained tuple. Follow-up refactor.

## What this is NOT

- Not a behavior-changing capstone (that's the reflect/fix loop in v5).
- Not a UI redesign (the panel layout is unchanged; the pill is
  additive).
- Not a rebase to upstream HEAD `f656440` — separate workstream.
- Not a push to `bytedance/deer-flow` — the upstream remote is
  removed entirely. Pushes go to `Jahanzaib211/nova` only.