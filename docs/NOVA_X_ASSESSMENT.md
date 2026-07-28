# Nova X — Architectural Assessment & Unification Roadmap

**Date:** 2026-07-17 · **HEAD:** `9f7cf8a3` · **Scope:** Parts 1–2 of the Nova X brief,
plus the Part-12 roadmap for everything C10's 18 items do not cover.
**Method:** every claim below was verified against the repository this session — file
paths and line counts are real, not estimated. Scores are deliberately blunt.

---

## Part 1 — Current Architecture Assessment

### 1.1 Frontend

**Stack:** Next.js (App Router), React, TanStack Query 5, `@langchain/langgraph-sdk`
streams, Tailwind. 180 `.tsx` + 149 `.ts` files. No client-state library (no zustand /
jotai / redux) — deliberate or not, all cross-cutting state lives in component trees.

**Structure:**

- `src/app/` — routes: `workspace`, `saas`, `blog`, `terms`, `privacy`, `(auth)`, `api`,
  `[lang]` i18n segments. Clean separation of marketing vs product surfaces.
- `src/core/` — 30+ feature modules (threads, sandbox, models, agents, credits, memory,
  mcp, skills, streamdown, …), each with its own `api.ts`/`hooks.ts`. Consistent pattern,
  good cohesion at the module level.
- `src/components/workspace/` — the product; `components/ui` — primitives.

**The two load-bearing problems, measured:**

1. **`agent-computer-panel.tsx` is 2,738 lines** containing all seven tabs (Files,
   Terminal, Editor, Browser, Activity, Review, Privacy) as inline components plus their
   data-fetch wiring, switched by `activeTab ===` conditionals (lines 2584–2701). Every
   C10 item lands in this file unless it is decomposed first. This single file violates
   the project's own 800-line rule by 3.4×.
2. **State flows by prop-drilling, not by a shared workspace model.**
   `chat-box.tsx` (287 lines) threads ~10 props (`activityEvents`, `todos`,
   `taskProgress`, `verifyResult`, `messages`, `artifacts`, …) into the panel; the panel
   re-derives per-tab state from them. `core/sandbox/hooks.ts` exposes **12 independent
   polling hooks** (`useSandboxLogs`, `useSandboxReview`, `useDevServerStatus`,
   `useBrowserCheck`, `useSandboxFiles`, …) each with its own interval and cache key —
   the "workspace" exists only as the union of whatever hooks a component happens to
   mount. `useSandboxTodo` is dead code (defined, never mounted).
   `core/threads/hooks.ts` is 2,504 lines — the de-facto state engine, unlabeled.

**Design language:** consistent Tailwind tokens, light/dark, i18n via `[lang]` +
message catalogs. The panel chrome reads as one product; the *data* underneath it does
not (each tab loads, errors, and refreshes independently, visibly).

### 1.2 Backend

**Stack:** FastAPI gateway (28 routers) + `deerflow` harness package with a strict
one-way boundary (harness never imports app — CI-enforced by `test_harness_boundary`).

**Strong, recently-hardened foundations (all verified green this session):**

- **Service layer:** `ServiceContainer` with 10+ DI services (protocols →
  implementations → container), including the now-live
  `WorkspaceIntelligenceService`.
- **Workspace Intelligence Kernel (WIK):** scanners, detectors, AST parsers,
  WorkspaceGraph, SymbolIndex, CommandRegistry, planner + risk analyzer + validator,
  TTL'd cache, DomainEvent-integrated events, metrics — exposed at
  `/api/workspace/{thread_id}/*` (7 endpoints, feature-flagged off, owner-checked).
- **Execution Kernel:** policy-classed supervised execution (SHELL/GIT/PYTHON/DOCKER/
  PM2/SYSTEMD), used by WIK's `execute_plan` behind a real risk gate.
- **Event bus:** typed synchronous DomainEvent bus with history; WIK events now flow
  into it (audit C6 fix).
- **Auth:** session auth + permission decorators + per-user ownership (B3 closed:
  agents Gate 4, models Gate 5), rate-limit tiers (auth/cost), CSRF, credits/billing.

**Duplicated logic / missing abstractions (the honest list):**

- Per-tab observation endpoints (`/api/sandbox/logs|todo|status`, browser-health,
  capabilities, workspace) each invent their own polling contract — four ways to ask
  "what is the thread doing." There is no single "workspace state" read model; the
  frontend's 12 polling hooks mirror this fragmentation 1:1.
- Two parallel "workspace" services (`WorkspaceService` and
  `WorkspaceIntelligenceService`) with overlapping names and unrelated scopes.
- Config assembly (config.yaml + runtime_models.yaml + extensions_config.json + DB
  ownership rows) has grown 4 sources of truth for "what models/tools exist."

### 1.3 Agent Architecture

**How it works together:** lead agent (LangGraph `create_agent`) + **19 middlewares in
strict order** (thread data → uploads → sandbox → …skill activation → summarization →
todo → tokens → title → memory → …clarification last) + subagent executor (dual
thread-pool, checkpointer-isolated) + task tool + skills + MCP (deferred tool loading) +
per-user memory with debounced LLM extraction. Verification exists (`dev_verify`,
browser checks, `/review` in sandbox); planning exists twice (TodoMiddleware for the
conversation; WorkspacePlanner in WIK for structured execution plans — not yet
connected to each other).

**Where friction actually is:**

- **Agents rediscover the workspace every turn.** Nothing injects WIK's snapshot into
  the agent context; the model runs `ls`/`grep`/`cat` through sandbox tools for facts
  the SymbolIndex already holds. The `WorkspaceMiddleware` interception layer designed
  in C9 (§1.7 of that plan) was never built — it is the single highest-leverage missing
  piece on the backend.
- **Two planners, no bridge:** `write_todos` plans in prose; `ExecutionPlan` plans in
  typed steps with risk levels. The UI shows the former; the safety machinery guards
  the latter.
- **Events vs. SSE streams:** run progress reaches the UI via the stream bridge;
  domain events (now including WIK's) stay server-side. Activity is reconstructed
  client-side from tool-call messages rather than consumed from the bus.

---

## Part 2 — Workspace Scorecard

| Category | Score | Why (blunt) |
|---|---|---|
| Workspace (foundation) | **8/10** | Per-thread isolation, virtual paths, uploads/outputs, WIK indexing — genuinely strong. Loses 2: intelligence is dormant (flag off, zero UI consumers). |
| Editor | **7/10** | Live write-view + diff works; no symbol context, no outline, buried in the panel monolith. |
| Terminal | **8/10** | Real ttyd interactive shell + streamed events — rare and good. No command history/registry surface. |
| Browser | **8/10** | VNC live view + preview + health checks. Toolbar shows a URL, not the verification state the backend already has. |
| Planner | **7/10** | Todo middleware solid in-conversation; typed ExecutionPlan machinery excellent but disconnected from it and from the UI. |
| **Workspace Cohesion** | **4/10** | Seven tabs = seven independent data islands in one 2,738-line file. Nothing follows the "current file/task" across tabs. |
| State Awareness | **4/10** | No central workspace state on either side. 12 polling hooks + prop drilling; `useSandboxTodo` dead; server has no unified read model. |
| Context Awareness | **5/10** | Memory + summarization + skills injection are real; but the agent's *workspace* context (symbols, commands, structure) is re-derived by shell every turn. |
| Token Efficiency | **4/10** | Repeated `ls`/`grep`/`cat` rediscovery; full file reads where the SymbolIndex would answer; WIK cache built but unconsumed by agents. |
| UI Cohesion | **5/10** | Visual language consistent; behavioral cohesion absent (independent loading/error/refresh per tab; no cross-tab synchronization). |
| Agent Awareness | **4/10** | Agents don't know the dev server is running, what was verified, or what the workspace graph knows. No WorkspaceMiddleware. |
| Navigation | **6/10** | Tab switching is fine; nothing is contextual (selecting a file changes nothing elsewhere). Search is file-name only. |
| Developer Experience | **6/10** | Backend DX strong (typed services, TDD culture, 5,793 tests). Frontend DX dragged down by the two monolith files (2,738 + 2,504 lines). |

**Verdict:** Nova's *capabilities* score 7–8; its *cohesion* scores 4–5. The brief's
diagnosis is correct: the limiting factor is unification, not features. The backend
for unification now exists (this week's work); nothing consumes it yet.

---

## Part 3 — Roadmap (Part 12 of the brief; what to build, in order)

### Covered by C10 (approved, next up — Phase 4 of the master plan)

- **Batch 0 (foundation, do first):** `WorkspaceStateProvider` in the frontend — one
  provider owning thread workspace state (snapshot, activity, todos, verification,
  dev-server), fed by the existing hooks consolidated + `/api/workspace/*`. Kills the
  chat-box prop-drill, revives `useSandboxTodo`, and **decomposes
  agent-computer-panel.tsx into per-tab files** (≤800 lines each) — the precondition
  for every other batch.
- **Batches 1–3:** the 18 items (Files→Workspace card, symbol tree, command discovery,
  Activity from workspace events, Review risk surface, Browser/Terminal/Editor
  enrichment, unified search, polish). Flip `workspace.intelligence_enabled: true`
  when Batch 1 ships.

### Not covered by C10 — the post-C10 arc, prioritized by impact

1. **WorkspaceMiddleware (agent awareness — highest leverage).** Inject a compact
   snapshot summary (projects, key symbols, commands, dev-server state) into the agent
   context when fresh; route `grep_files`/`search_files` through SymbolIndex with
   bounded-walker fallback. This is C9 §1.7, still unbuilt. Expected effect: the
   Token Efficiency and Agent Awareness scores are both gated on it. *Effort: M.
   Risk: prompt-size regressions — ship behind the same feature flag.*
2. **Unified timeline v2.** Bridge DomainEvents (now including WorkspaceScanned/
   PlanBuilt/CacheHit) into the run SSE stream so Activity becomes the replay of the
   session (plan → research → edit → verify → review → commit), not a tool-call log.
   *Effort: M.*
3. **Planner bridge.** When the todo middleware plans, materialize an `ExecutionPlan`
   alongside; surface risk level in Review; require the existing approval gate for
   HIGH steps. One planning model, two views. *Effort: M–L.*
4. **workspace.* client SDK.** Typed frontend client wrapping `/api/workspace/*` +
   sandbox observation endpoints behind one `workspace.open/search/state()` surface —
   retiring the 12-hook sprawl module by module. *Effort: S–M, incremental.*
5. **Engineering memory graph.** Extend WorkspaceGraph with commits/reviews/
   verification nodes (git log is already local; review results already exist).
   Defer until 1–4 prove the consumption pattern. *Effort: L.*
6. **Invisible intelligence.** Auto-sequencing (fix → browse → verify → review) from
   plan templates. Only after the planner bridge exists. *Effort: L.*

### Breaking changes & risks

- None of 1–4 requires breaking changes; all ship behind `workspace.intelligence_enabled`.
- Frontend rebuild required to land any UI batch (prod-baked image — one rebuild per
  batch group, not per item).
- The panel decomposition (Batch 0) is the only high-merge-risk item; do it while no
  other frontend work is in flight, in one commit.

---

*Assessment grounded in: 5 commits landed 2026-07-17 (`8a62add5`→`9f7cf8a3`), full-suite
runs (5,793 passed), and direct file inspection. Prior context: `c9-workspace-intelligence-kernel.md`,
`nova-consolidated-audit-2026-07.md`, master plan `reconciliation-my-reads-vs-cosmic-zebra.md`.*
