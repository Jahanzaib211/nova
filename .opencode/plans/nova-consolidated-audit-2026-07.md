# Nova — Consolidated Audit, Verdicts & Fix Backlog

**Compiled:** 2026-07-17 · **Branch:** `feat/nova-plus-ops-console` · **Status:** execution in progress

This doc absorbs three research streams into one source of truth: (1) a full regression
audit, (2) an assessment of a "Nova Capability Inventory" self-report, (3) a full-stack
security audit. Each claim below is tagged by how it was verified.

## 0. Verification legend

- **VERIFIED** — personally re-checked against code at the cited `file:line` this session.
- **CODE-BACKED** — came from an audit agent with a `file:line` citation; trustworthy but
  not personally re-verified. Verify before fixing.
- **LATENT** — real in code but on a dormant path (WIK is dead code); fix only when wired.

---

## 1. "Nova Capability Inventory" — hallucinated or real?

**Verdict: tool list REAL, status column CONFABULATED.**

- The tool inventory is real — all 10 sampled tools (`system_probe`, `deploy_expose`,
  `present_files`, `ask_clarification`, `scaffold_project`, `dev_verify`,
  `browser_navigate`, `free_port`, `shell_session`, `code_review`) resolve to real files
  in `backend/packages/harness/deerflow/` (manifest.py, tools/builtins/). Skills list matches. **VERIFIED**
- The per-tool ✅/⚠️/❌ status is unreliable: internal contradictions (same tools marked
  both "untried" and "fails"; Files summary says "4 broken" but lists 6; screenshot
  "known to lie"). An agent that had actually probed wouldn't contradict its own counts.
- "Sandbox DOWN (Docker port exhaustion)" is plausibly real (matches machine's Docker/mem
  pressure) but the specific pass/fail breakdown is confabulated confidence.

**Takeaway:** trust *what tools exist*, not the self-reported *what-works-now* column.

---

## 2. Regression audit — NO regressions

- `git status` clean on `feat/nova-plus-ops-console`; source intact (the earlier `.next`
  delete crashed the running container, not the repo).
- Containers healthy: frontend, gateway, autoheal, nginx (unrelated fox/genesis/medusa
  sidecar noise is pre-existing, not Nova).
- Backend suite: **5399 passed / 1 failed / 16 skipped** (328 live/network deselected).
  The 1 failure — `test_subagent_executor.py::TestSkillAllowedTools::test_empty_allowed_tools_contributes_no_tools`
  — was initially diagnosed as a test-fixture bug. **Root cause (found + fixed 2026-07-17):
  a real production circular import** — `deerflow.subagents.executor` could not be imported
  in a clean interpreter (`executor → agents.thread_state → agents/__init__ → lead_agent →
  subagent_limit_middleware → executor`). Fixed by: `MAX_CONCURRENT_SUBAGENTS` moved to
  `subagents/config.py` (leaf), lazy PEP 562 exports in `deerflow/agents/__init__.py`
  (skills-cache priming moved to `lead_agent/__init__.py`), prompt.py imports the registry
  directly. The conftest.py `sys.modules` executor mock and the test file's mocked-module
  machinery are **removed** — executor tests now run against real modules. Pinned by
  `tests/test_import_hygiene.py` (subprocess-based clean-import regression tests).
- Frontend: `pnpm typecheck` exit 0; `pnpm test` 457/457. Validates source on disk, not
  the running prod image (see §5).
- Nova v9 feature tests all green: credits 12, referrals 6, byok 10, billing 14,
  legal_consent 5, admin_users 14, credit_requests 5, update_email → 66 passed. WIK backend
  `test_workspace_kernel.py` → 48 passed.

---

## 3. Security audit — findings

Severity: 🔴 critical · 🟠 high · 🟡 med · 🟢 low.

### Tier 1 — exploitable now (ALL VERIFIED this session)

| # | Sev | Finding | Location | Status |
|---|-----|---------|----------|--------|
| A1 | 🔴 | Stored/reflected XSS: `rehypeRaw` with **no `rehypeSanitize`** on the main markdown path; untrusted LLM/tool/web output renders as live DOM. | `frontend/src/core/streamdown/plugins.ts:15` | VERIFIED |
| A2 | 🔴 | Anonymous stateless runs bypass credit wall + ownership: `POST /api/runs/stream` & `/wait` lack `@require_permission` (their thread-scoped twins have it). | `backend/app/gateway/routers/runs.py:35,60` | VERIFIED |
| A3 | 🟠 | Unauth debug endpoint `/_diagnostics/stream-trace` — no auth/CSRF, only env-flag gated; leaks stream-trace ring when `DEER_FLOW_STREAM_TRACE` on. | `backend/app/gateway/routers/thread_runs.py:492` | VERIFIED |
| A4 | 🟠 | Open-redirect: caller-supplied Stripe `success_url`/`cancel_url` forwarded with no host allowlist. | `backend/app/gateway/routers/billing.py:61` | VERIFIED |

### Tier 2 — real gaps

| # | Sev | Finding | Location | Status |
|---|-----|---------|----------|--------|
| B1 | 🟠 | No rate limit on credit-consuming endpoints (`/api/runs/*`, `/threads/{id}/runs/*`, iGIN0 `/research`, `/suggest`); with A2 → unthrottled LLM spend. | `auth_rate_limit_middleware.py` (3 paths only) | CODE-BACKED |
| B2 | 🟡 | Channel topology disclosure: `GET /api/channels/` returns live status to any authed user (admin guard only on restart). | `routers/channels.py:29` | CODE-BACKED |
| B3 | 🟠 | Model/agent write endpoints lack permission check — any authed user can add/customize models & agents. | `routers/models.py:219+`, `routers/agents.py:192+` | CODE-BACKED |
| B4 | 🟡 | Stack-trace leak: raw exception text as 500 detail. | `routers/mcp.py:384` | CODE-BACKED |
| B5 | 🟡 | Risk false-safe: `RiskAnalyzer` is substring keyword match; split/var commands bypass; `execute_plan` has no risk gate. | `workspace/planner/risk_analyzer.py:70` | LATENT (WIK) |
| B6 | 🟡 | WIK cache key collision (SHA-256 truncated 16 hex) + non-thread-safe CacheEntry mutation. | `workspace/cache/cache_key.py:44` | LATENT (WIK) |
| B7 | 🟠 | `SKIP_ENV_VALIDATION=1` baked into prod image → missing `NEXT_PUBLIC_*` silently baked; runtime-only failure. | `frontend/src/env.js:49` | CODE-BACKED |

### Tier 3 — latent / hardening

| # | Sev | Finding | Status |
|---|-----|---------|--------|
| C1 | 🟡 | WIK is dead code — `WorkspaceIntelligenceServiceImpl` never invoked. Wiring `scan()` as-is = blocking IO on event loop (violates CI gate). | LATENT |
| C2 | 🟡 | No hard token/budget ceiling — only recursion/turn caps + fail-open quota probe. | REAL |
| C3 | 🟢 | Subagent cancellation cooperative-only (in-flight tool not interrupted until timeout). | REAL |
| C4 | 🟢 | `str_replace`/`write_file` not atomic (no temp+rename). | REAL |
| C5 | 🟢 | `plan_validator` ignores depends_on/cycles/depths; `plan_search` symbol resolution dead. | LATENT (WIK) |
| C6 | 🟢 | WIK events dropped silently (frozen `WorkspaceEvent` ≠ `DomainEvent`); `_record_scan` StopIteration on cache-hit. | LATENT (WIK) |
| C7 | 🟢 | `useSandboxTodo` dead code; `_scan` off-by-one depth; 500→404 masking on checkpointer error (`threads.py:409`). | REAL |
| C8 | 🟢/info | Local-sandbox host-bash escape is by-design/config-gated. AIO (Docker) is the real boundary. | INFO |

---

## 4. Fix execution order

1. **A1** — add `rehype-sanitize` to `streamdownPlugins` (allowlist schema that keeps
   katex/mermaid classes). Frontend — committed now, **lands only on prod rebuild** (§5).
2. **A2** — add `@require_permission("runs","create")` to `POST /api/runs/stream|wait`
   (mirror `thread_runs.py`). Check call-sites first (auth-enabled prod → safe).
3. **A3** — gate `/_diagnostics/stream-trace` behind `require_admin_user`.
4. **A4** — allowlist Stripe redirect hosts (relative-path or same-origin only).
5. **B1** — extend rate-limit middleware to run/credit/research/suggest paths.
6. **B7** — drop `SKIP_ENV_VALIDATION` in prod image / fail-fast at container start.
7. **B3 / B2 / B4** — permission-gate model/agent writes; restrict channel-status
   disclosure to admin; sanitize mcp 500 detail.
8. **C2 / C4 / C7** — token ceiling, atomic file writes, checkpointer-error status.
9. **WIK-gated (B5,B6,C1,C5,C6)** — only when WIK is wired (see §6). Do NOT touch dead code.

Each fix: TDD where a test harness exists, targeted test run (not full suite — memory),
incremental commit on `feat/nova-plus-ops-console`.

### 4b. Applied 2026-07-17 (status)

| # | Status | Note |
|---|--------|------|
| A1 | ✅ done (source) | rehype-raw → **sanitize** → katex; `rehype-sanitize@6` added; frontend typecheck 0 errors. **Lands on prod rebuild** (§5). |
| A2 | ✅ done + live | `@require_permission("runs","create")` on both stateless endpoints. Verified frontend/channels don't call them. |
| A3 | ✅ done + live | `require_admin_user` on `/_diagnostics/stream-trace`. |
| A4 | ✅ done + live | `_safe_redirect()` same-origin allowlist; billing tests green; unit tests added. |
| B1 | ✅ done + live | Cost tier (`/runs/stream`, `/runs/wait`, `/research`, `/suggestions`), IP-keyed, env-tunable (`NOVA_RUN_RATE_MAX`/`_WINDOW`, default 60/60s). Unit tests added. |
| B4 | ✅ done + live | mcp 500 detail no longer echoes exception text (still logged with `exc_info`). |
| B7 | ✅ done (source) | Removed `SKIP_ENV_VALIDATION=1` from `frontend/Dockerfile` — schema is all-optional so build still passes, validation re-enabled. Lands on rebuild. |
| B2 | ⛔ **deferred — not a bug** | `test_channels_router::test_get_channels_status_remains_read_only` asserts non-admin **200**: read-only channel status is a deliberate design choice. Admin-gating would break that contract. Revisit only as a product decision. |
| B3 | 🟡 **in progress — Gate 4 (agents) done 2026-07-17** | Frontend settings UI actively creates/updates/deletes models & agents (`core/models/api.ts`, `core/agents/api.ts`). Admin-gating breaks it for all 24 users. Decision: per-user scoping of models/agents (owner column, nullable→backfill→enforce migration; reads = owner OR shared/system, writes = owner, admin manages all). Scheduled as Phase 3 in `~/.claude/plans/reconciliation-my-reads-vs-cosmic-zebra.md`. |
| B5,B6 | ⛔ WIK-latent | Fix only when WIK is wired (§6). |
| C2 | ⏳ deferred | Hard token/budget ceiling — substantial feature overlapping the credits system; not a drop-in. |
| C4 | ⏳ deferred | Atomic `str_replace`/`write_file` (temp+rename). Real, but on the agent file-write **hot path** (`local_sandbox.py:433`); 🟢 low sev — needs a careful pass + blocking-IO gate + full sandbox suite (heavy). Do deliberately, not under live hot-reload. |
| C7 | ✅ not reproduced | `threads.py:405-409` already raises 500 on checkpointer error / 404 only on genuine not-found. Audit line ref stale; nothing to fix. |

**Live gateway** (uvicorn --reload) verified healthy (200 via nginx) after each backend edit.
New tests: `backend/tests/test_security_audit_hardening.py` (5 passed).

---

## 5. Tracked in-flight items (folded in, not blocking security work)

- **Frontend UI changes NOT live.** Frontend runs `next start` from a **prod-baked image**
  (`.next` from Jul 12), so all v9 UI (credits meter, 402 toast, `/terms` `/privacy`,
  consent checkbox, referral/BYOK settings) + the A1 fix are committed to source but not
  running. Landing requires `docker compose build frontend` + recreate.
  - **Build-blockers to clear first:** `frontend/.next` is **root-owned** (Jul 12 container
    write) → host `pnpm build` EACCES on `.next/trace`. Stray `/home/jahanzaib/pnpm-lock.yaml`
    confuses Next workspace-root detection. Docker build re-runs full `pnpm install`
    (1077 pkgs @ ~45 KiB/s) — impractical without cache. Same family: root-owned `.pytest_cache`.
  - **Options:** fix perms + lockfile then host `pnpm build` + `docker cp .next` + restart
    (fast, non-durable); OR `docker compose build frontend` (durable, slow/memory-heavy).
    Memory-gated — ASK before running (standing rule: never kill/restart without asking).
- **Ops console (`:4100`) Requests tab** — needs a console rebuild to go live (independent,
  lighter than the Nova frontend).

## 6. WIK / C10 note

The WIK backend (`WorkspaceIntelligenceServiceImpl`) is constructed lazily but **never
invoked** — all WIK findings (B5, B6, C1, C5, C6) are latent until wired. The earlier C10
"Workspace Unification" plan must, before any UI consumes it: run `scan()` via
`asyncio.to_thread` (off the event loop, or it trips the `detect-blocking-io` CI gate),
add a real risk gate (not substring match), fix the cache key/locking, and fix the
`WorkspaceEvent`/`DomainEvent` typing. Fold this hardening into C10's backend step.
