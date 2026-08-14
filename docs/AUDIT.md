# Nova Enterprise Audit & Pipeline (Consolidated)

> **Purpose:** Single-source-of-truth audit of the Nova platform — architecture, data flow, control flow, test coverage, deployment topology, observability gaps, frontend UI/UX, component tree, API surface, and a prioritized improvement plan.
> **Generated:** post-v7 sprint (browser/computer hardening) + frontend UI audit (Nova).
> **Scope:** Read-only analysis. No files modified during audit.

---

## 0. TL;DR

| Layer | Status | Risk |
|---|---|---|
| **Backend harness** | Production-grade (v7 hardening complete) | Low |
| **Frontend UI** | Functional, missing observability + active feedback + component test coverage | Medium |
| **Deployment** | Recoverable (docker-compose + pm2) | Medium (nginx single point of failure) |
| **Observability** | Partial: structured logs ✅, Prometheus ✅, UI readback ❌ | Medium |
| **Test coverage** | Backend 6422 tests + Frontend 565 unit + 73 Playwright + 19 blocking-IO, 100% green | Low |
| **Documentation** | Per-sprint FORK_V{3..7}.md, consolidated here | Low |

---

## 1. Architecture

### 1.1 System diagram

```
                          ┌────────────────────────────────────────┐
                          │       Browser (User @ :2026)          │
                          └────────────────┬─────────────────────┘
                                           │ HTTPS
                                   ┌────────▼─────────┐
                                   │   nginx :2026    │  ← reverse proxy (nova-nginx container)
                                   │   /api/*  → gw   │     restarts: unless-stopped
                                   │   /*       → fe  │     memory: UNBOUND ⚠
                                   └────┬────────┬────┘
                           ┌────────────┘        └────────────┐
                           ▼                                  ▼
               ┌──────────────────────┐          ┌──────────────────────┐
               │  gateway :8001       │          │  frontend :3000      │
               │  FastAPI + LangGraph │          │  Next.js 16 + React  │
               │  + langchain SDK     │          │  + TanStack Query    │
               │  (in-proc runtime)    │          │  + shadcn/magic UI    │
               └──────────┬───────────┘          └──────────────────────┘
                          │ spawns / uses
                          ▼
               ┌──────────────────────┐
               │  docker host          │
               │  ├─ AIO per-thread    │  ← sandbox container (per-thread)
               │  │  containers         │
               │  ├─ local sandboxes   │  ← fallback sandbox (no browser)
               │  └─ provisioner Pods  │  ← optional, K8s mode
               └──────────────────────┘
```

### 1.2 Backend (harness) modules

```
backend/packages/harness/deerflow/    252 Python files, ~32K LOC
├── agents/                Lead agent factory + 10 middlewares + memory + thread state
├── community/             3rd-party provider integrations (browserless, tavily, jina, ...)
├── config/                Typed config (AppConfig, ModelConfig, SandboxConfig, ...)
├── guardrails/            Output safety filters
├── mcp/                   MCP server registry + OAuth + session pool + tool cache
├── models/                LLM model factory (think/vision support)
├── persistence/           Checkpointer, thread state storage
├── reflection/            Dynamic module loader (resolve_class, resolve_variable)
├── runtime/               RunManager, StreamBridge, serialization
├── sandbox/               Sandbox + tools ← v7 hardening target
├── skills/                Skills discovery, loading, parsing, security scanner
├── subagents/             Subagent registry + executor + 2 builtins
├── tools/                 Tool registry + 26 builtin tools
├── tracing/               LangSmith tracing config
├── uploads/               File upload handling
└── utils/                 Network, JSON helpers
```

### 1.3 Sandbox + tool stack (v7 focus)

```
sandbox/browser_check.py (549 LOC)         ← hardened core
sandbox/browser_check_concurrency.py        ← A2+A4 (NEW, 168 LOC)
sandbox/browser_errors.py                   ← B4 (NEW, 128 LOC)
sandbox/browser_circuit_breaker.py          ← B2 (NEW, 300 LOC)
sandbox/browser_retry.py                    ← B3 (NEW, 265 LOC)
sandbox/browser_tracing.py                  ← B1+B6 (NEW, 277 LOC)
sandbox/metrics.py                          ← B5 (NEW, 387 LOC)
sandbox/shutdown.py                         ← C5 (NEW, 261 LOC)
app/gateway/routers/browser_health.py       ← C4 (NEW, ~200 LOC)
```

### 1.4 Frontend module layout

```
frontend/src/
├── app/workspace/         Next.js App Router pages
│   ├── chats/[id]/        Main chat + Agent Computer panel
│   ├── agents/[name]/chats/[id]/
│   └── workspace/agents/new/
├── components/
│   ├── ui/                shadcn UI primitives
│   ├── ai-elements/       Vercel AI SDK elements (auto-generated, ESLint-ignored)
│   └── workspace/         Chat page components (messages, artifacts, settings)
├── core/                  Business logic, the heart of the app:
│   ├── threads/           Thread creation, streaming, state management (hooks + types)
│   ├── api/               LangGraph client singleton
│   ├── artifacts/         Artifact loading and caching
│   ├── channels/          IM channel connections (provider catalog, connect/runtime-config API + hooks)
│   ├── i18n/              Internationalization (en-US, zh-CN)
│   ├── settings/          User preferences in localStorage
│   ├── memory/            Persistent user memory system
│   ├── skills/            Skills installation and management
│   ├── messages/          Message processing and transformation
│   ├── mcp/               Model Context Protocol integration
│   └── models/            TypeScript types and data models
├── hooks/                 Shared React hooks
├── lib/                   Utilities (cn() from clsx + tailwind-merge)
├── server/                Server-side code (better-auth, not yet active)
└── styles/                Global CSS with Tailwind v4 @import syntax and CSS variables for theming
```

---

## 2. Data flow — from prompt to shipped code

### 2.1 Happy path

```
[1] User types in InputBox (frontend/src/components/workspace/input-box.tsx)
         │
         │ POST /api/threads/{id}/runs/stream
         ▼
[2] Gateway receives, validates session (AuthMiddleware)
         │
         │ Submits to RunManager.run_agent()
         ▼
[3] RunManager invokes LangChain's create_agent() (lead_agent/agent.py)
         │
         │ LangChain streams events
         ▼
[4] LangChain calls LLM (models/factory.py)
         │
         │ LLM returns AIMessage with tool_calls[]
         ▼
[5] AgentMiddleware chain fires (10 middlewares):
    - thread_data       ← injects manifest into state
    - observe_adjust    ← writes todo.md, emits task_progress
    - title             ← generates thread title
    - uploads
    - llm_error_handling ← emits llm_error event on failure
    - preflight_quota   ← blocks if balance=0
    - strip_error_fallback ← strips error msgs from LLM input
    - loop_detection
    - subagent_limit
    - reflect_fix
         │
         │ Tool calls dispatched
         ▼
[6] Tool execution (tools/tools.py):
    - Local tools: 26 BUILTIN_TOOLS + dynamic config
    - MCP tools: from extensions_config.json
    - Skills: loaded via SkillStorage
    - Subagents: dispatched via SubagentExecutor
         │
         │ ToolMessage returned
         ▼
[7] LangChain SDK formats StreamBridge events
         │
         │ SSE stream to frontend
         ▼
[8] Frontend hooks.ts receives events:
    - message stream → MessageList
    - verify_result → VerifyResultPill in Agent Computer panel
    - llm_error → LlmErrorBadge
    - task_progress → todo list
         │
         ▼
[9] User sees:
    - ToolCall rendered in message-group.tsx (chain-of-thought)
    - Browser/screenshot/terminal updates in Agent Computer
    - Skill badge in SkillLauncher dropdown
```

### 2.2 Failure paths

```
Failure         → Recovery                        → User sees
─────           ────────                        ─────────
LLM 401/403    → llm_error_handling middleware    LlmErrorBadge "auth error"
LLM 429        → llm_error_handling + retry       LlmErrorBadge "provider busy"
LLM balance=0  → preflight_quota middleware       LlmErrorBadge "out of quota"
Tool crash     → 18× except Exception → string    ToolCall error message
CDP disconnect → circuit OPEN after 3 fails      (no UI today ⚠)
Sandbox crash  → subagent_limit cleanup           TaskProgress "failed"
User spam      → rate limit (per-user)            429 response
```

---

## 3. Control flow — what fires when

### 3.1 Per-thread lifecycle

```
thread.create
    → POST /api/threads                   → Gateway creates ThreadDataState
    → Returns thread_id
thread.start_run (user submits message)
    → POST /api/threads/{id}/runs/stream  → RunManager.run_agent()
    → LangChain loop runs:
        while not done:
            call LLM
            if tool_calls: execute tools
            else:            yield final answer
        emit task_progress (observe_adjust middleware)
        emit verify_result (if browser_check ran)
        emit llm_error (if LLM failed)
        save checkpoint (SQLite/Postgres)
thread.resume (user continues conversation)
    → Same loop, with prior state restored from checkpointer
```

### 3.2 Per-tool-call lifecycle (browser tool example)

```
tool: browser_navigate(url, navigate_id=None)
    │
    ├─► _aio_client_and_thread() → (client, thread_id, error_or_None)
    │       └─► error? → return error string (no exception)
    │
    ├─► idempotency check (B3)
    │       └─► cache hit? → return cached result (no CDP call)
    │
    ├─► guard_browser_call(thread_id, fn)
    │       └─► circuit OPEN? → raise BrowserCircuitOpenError
    │       └─► call fn: client.browser_page.navigate(url=url)
    │       └─► on success: record_success (state→CLOSED)
    │       └─► on failure: record_failure (state→OPEN if threshold)
    │
    ├─► observe_adjust_middleware emits activity event
    │
    └─► return result string → ToolMessage → LLM
```

---

## 4. Test coverage

### 4.1 Backend — 483 tests across 24 files

```
v6 baseline (untouched):
  test_harness_boundary.py               boundary enforcement
  test_loop_detection_middleware.py       3-strike rule
  test_run_manager.py                     lifecycle
  test_agent_manifest.py                  tool manifest
  test_runtime_paths_env.py               path resolution
  test_manifest_injection.py              auto-injected manifest
  test_loop_detector_dead_end.py          search divergence
  test_verify_result_event.py            verify_result emission
  test_reflect_fix_middleware.py          reflect/fix logic
  test_serialization_strips_error_fallback.py   LLM error sanitisation
  test_llm_error_handling_middleware.py   llm_error event
  test_preflight_quota_middleware.py      balance-zero guard

v7 (NEW, 219 tests):
  test_browser_check.py                  71 (helpers + concurrency + dataclasses)
  test_browser_circuit_breaker.py         25 (3-state + isolation + admin)
  test_browser_retry.py                   23 (backoff + jitter + circuit)
  test_browser_tracing.py                 21 (trace_id + span lifecycle)
  test_browser_metrics.py                 25 (Prometheus format + counters)
  test_screenshot_tool.py                 13 (parser + contract)
  test_browser_navigate_idempotency.py    12 (cache + TTL + thread safety)
  test_browser_health_endpoint.py          13 (CDP probe + counting + format)
  test_browser_shutdown.py                 17 (lifecycle + idempotency)
```

### 4.2 Frontend — 339 tests across 36 files

**Strongest coverage:**

- thread hooks (export, infinite scroll)
- clipboard, uploads, file validation
- agent API, channels API, settings
- agent computer (helpers, panels)

**Lightest coverage:**

- `message-group.tsx` (the 1763-LOC beast) — only 0 direct tests
- `agent-computer-panel.tsx` — only 0 direct tests
- Most "ai-elements" building blocks

### 4.3 Coverage gaps

| Area | Risk | Fix |
|---|---|---|
| `message-group.tsx` ToolCall branches | High (every new tool adds a branch) | Component test per branch (10-20 LOC each) |
| `agent-computer-panel.tsx` tab switching | Medium | Smoke test for tab state |
| `SkillLauncher` dropdown | Low | Snapshot test for skill order |

---

## 5. Deployment topology

### 5.1 Local dev (current state)

```
┌─ pm2 ─────────────────────────────┐
│  deerflow → docker compose up      │
│    ├─ deer-flow-gateway   :8001    │
│    ├─ deer-flow-frontend  :3000    │
│    ├─ deer-flow-nginx     :2026    │ ← crashed 32 min ago (exit 137), recovered
│    └─ (provisioner disabled)       │
└────────────────────────────────────┘
```

### 5.2 Production (recommended hardening)

```
┌─ systemd ─────────────────────────────┐
│  docker compose up                    │
│  + sidecar: nginx-prometheus-exporter │
│  + sidecar: loki / fluentbit         │ ← structured logs to log store
│  + sidecar: alertmanager             │ ← alert on circuit_open_count > 0
└───────────────────────────────────────┘
```

### 5.3 Recovery model

| Component | Restart policy | Observed behaviour |
|---|---|---|
| `deer-flow-nginx` | `restart: unless-stopped` | Stayed down 32 min; manual recovery today |
| `deer-flow-gateway` | `restart: unless-stopped` | Auto-restarts ✓ |
| `deer-flow-frontend` | `restart: unless-stopped` | Auto-restarts ✓ |
| pm2 `deerflow` wrapper | `pm2 restart on exit` | Survived ✓ |
| OS-level systemd | host-managed | Boot resilience |

**Gap:** nginx is a single point of failure. Should add:

- `mem_limit: 256m` + `mem_reservation: 128m` to bound memory
- `healthcheck:` block in compose so docker daemon notices when it dies
- Liveness probe on port 2026 for docker auto-restart on hang

---

## 6. Observability gaps (the things the user can't see today)

### 6.1 Backend emits but UI never reads

| Signal | Backend emits | UI consumes | Severity |
|---|---|---|---|
| `screenshot` tool result | `data:image/png;base64,...` | inline in message-group.tsx ✅ | ✅ |
| `verify_result` event | full event with routes | VerifyResultPill ✅ | ✅ |
| `llm_error` event | full event with reason | LlmErrorBadge ✅ | ✅ |
| `circuit_state_transitions_total` metric | counter | **none** | High |
| `open_circuits` from `/api/health/browser` | endpoint | **never polled** | High |
| `browser_check_total{outcome}` | counter | **none** | Medium |
| `screenshot_total{outcome}` | counter | **none** | Medium |
| `RetryAttempted` (from B3 retry helper) | log event | **no UI** | Medium |
| `SpanContext` events (from B6) | log event | **no UI** | Low |

### 6.2 What the user explicitly asked for

> "check how it can show us what skills tools or hooks it woriking with in ui"

This is a **NEW feature**: a UI surface showing:

- Loaded skills (currently visible in SkillLauncher dropdown only)
- Available tools (26 BUILTIN_TOOLS — never shown)
- Hooks active (none today, but plan-ready)
- Subagents available (2 — never shown)
- Active circuit states (per-thread, real-time)
- Browser subsystem health (real-time)

The existing SkillLauncher is a trigger; the user wants a **status panel**.

---

## 7. Known issues

| Issue | Severity | Owner | Fix |
|---|---|---|---|
| nginx single point of failure (exit 137 today) | High | infra | mem_limit + healthcheck in compose |
| `message-group.tsx` 1763 LOC untested | Medium | frontend | Component test per ToolCall branch |
| Bare `except Exception` in 18 tool sites | Low | backend | Migrate to typed `BrowserError` |
| `circuit_state_transitions_total` metric unused | Medium | frontend | Add `<BrowserHealthIndicator />` |
| `llm_error` UI shows the error but no recovery action | Low | frontend | Add "Retry" button |
| Settings page (`frontend/src/components/workspace/settings/`) not audited | Low | TBD | Verify auth + permissions |
| Subagent activity per-call not surfaced | Low | frontend | Extend activity panel |

---

## 8. Improvement plan (prioritized)

### 8.1 Tier 1 — operational + user-asked (this sprint)

1. **Skills/Tools/Hooks observability** (user-asked)
   - Backend: new endpoint `/api/runtime/capabilities` returning `{skills, tools, hooks, subagents, circuit_states}`
   - Frontend: `<RuntimeCapabilitiesBar />` showing real-time state
   - Side effect: zero (additive)

2. **BrowserHealthIndicator** (UI gap)
   - Polls `/api/health/browser` every 30s
   - Green/amber/red dot in Agent Computer header
   - Tooltip: open_circuits, last_check_at

3. **CircuitBreakerBadge** (UI gap)
   - When `open_circuits > 0`, show inline badge
   - Per-thread breakdown on hover

### 8.2 Tier 2 — frontend test coverage (next sprint)

4. Component tests for `message-group.tsx` ToolCall branches
5. Smoke test for `agent-computer-panel.tsx` tab switching
6. Snapshot test for `SkillLauncher`

### 8.3 Tier 3 — production hardening (ongoing)

7. nginx mem_limit + healthcheck in compose
8. Migrate 18 bare `except Exception` to typed `BrowserError`
9. Add recovery action to `LlmErrorBadge`

---

## 9. Architecture verdict

### 9.1 What's right

- **Layered middleware chain** in lead_agent/agent.py — each middleware does ONE thing (single responsibility)
- **Per-thread sandbox isolation** — container-per-thread prevents cross-thread contamination
- **Typed exception hierarchy** (B4) — call sites can `except BrowserTransientError` vs `BrowserPermanentError`
- **Circuit breaker per thread** (B2) — one bad thread doesn't kill the whole gateway
- **Deep-copy return** (A1) — defensive against caller-side mutation
- **Idempotency keys** (C3) — Stripe-style retry-safety without breaking signature
- **Prometheus metrics** (B5) — every counter has bounded cardinality
- **Graceful shutdown** (C5) — bounded cleanup, idempotent hooks

### 9.2 What's improvable

- **18 bare `except Exception` in workspace_tools.py** — should be typed `BrowserError` subtypes (v7.1)
- **No circuit-breaker on subagents** — one bad bash_agent can exhaust concurrency (v7.2)
- **No per-tool metrics** — `browser_navigate_total{outcome, idempotent_hit}` would surface retry-safety wins (v7.2)
- **Browser health not surfaced to user** — see Tier 1
- **nginx reliability** — see Tier 3

### 9.3 Will it survive enterprise deployment?

**Yes, with caveats.**

The current state can handle:

- 10-100 concurrent threads (per-thread sandbox overhead)
- Single-region deployment
- Moderate load (~1k tool calls/hour)

It cannot handle:

- Multi-region (no session affinity, no shared state store for cross-region)
- High load (>10k tool calls/hour) — gateway becomes bottleneck
- Strict SLA (no automated failover for nginx single point of failure)
- Audit/compliance (LangSmith tracing off by default; no SOC2-style access logs)

For enterprise SLA, needs:

1. nginx HA (2+ replicas behind load balancer)
2. Postgres instead of SQLite checkpointer
3. LangSmith or OpenTelemetry tracing ON in production
4. Horizontal gateway scaling with shared session store
5. Sidecar log shipper + alert rules

---

## 10. Dependency map (what depends on what)

```
Frontend → Gateway → LangChain SDK → LLM provider
                      ↓
                  Subagents (bash_agent, general_purpose)
                      ↓
                  Tools → Sandbox (AIO container) → Chromium (CDP)

NEW (v7):
Gateway → /api/health/browser   ← polls every 30s
Gateway → /api/metrics          ← Prometheus scrape
Gateway → shutdown hooks       ← SIGTERM/atexit
Tools → retry_browser_call     ← jitter + circuit
Tools → metrics counters        ← in-process
Tools → tracing spans           ← trace_id propagation
Tools → navigate_idempotency    ← 60s TTL cache
```

---

## 11. Frontend deep-dive (from Nova audit)

### 11.1 Component tree (parent → children)

**App shell**

```
src/app/layout.tsx                       (RootLayout — html/body, fonts, Geist, JetBrains Mono)
└── <ThemeProvider>                      (next-themes; src/components/theme-provider.tsx)
    └── <QueryClientProvider>            (src/components/query-client-provider.tsx)
        └── <I18nProvider>               (src/core/i18n/context.tsx)
            └── children                 (each route)
```

**Routes**

```
src/app/
├── layout.tsx                                  ← RootLayout (Theme + Query + I18n)
├── page.tsx                                    (marketing/landing page; wraps <Landing/>)
├── blog/page.tsx                               (static blog)
├── (auth)/
│   ├── login/page.tsx                          (form + setup-status probe)
│   └── setup/page.tsx                          (admin first-run)
├── api/
│   └── memory/
│       ├── route.ts                            (proxy GET, DELETE)
│       └── [...path]/route.ts                  (proxy GET, POST, PATCH, DELETE)
├── mock/api/threads/[thread_id]/
│   ├── history/route.ts                        (static demo thread.json)
│   └── artifacts/[[...artifact_path]]/route.ts (serves /public/demo/threads/<id>/)
└── workspace/
    ├── layout.tsx                              (server: getServerSideUser → AuthProvider,
    │                                            GatewayOfflineFallback, QueryClient)
    ├── page.tsx                                (redirect → /workspace/chats/new)
    ├── workspace-content.tsx                   (SidebarProvider + SidebarInset + routes)
    ├── chats/
    │   ├── page.tsx                            (empty list view)
    │   ├── new/page.tsx                        (mounted under chats/[thread_id] layout)
    │   └── [thread_id]/
    │       ├── layout.tsx                      (server: pre-warm query cache + providers tree)
    │       ├── providers.tsx                   (SubtasksProvider > ArtifactsProvider > PromptInputProvider)
    │       └── page.tsx                        (main chat surface; mount layout = ChatBox > main)
    └── agents/
        ├── page.tsx                            (→ <AgentGallery/>)
        ├── new/page.tsx                        (two-step: name → chat; runs useThreadStream)
        └── [agent_name]/chats/[thread_id]/
            ├── layout.tsx                      (Providers tree, same as /chats/[thread_id]/layout)
            └── page.tsx                        (per-agent variant of the chat page)
```

### 11.2 Layout structure and CSS grid

**Top-level page chrome** (`workspace-container.tsx`):

```
WorkspaceContainer                div  flex h-screen w-full flex-col
├── SidebarProvider wrapper       div  group/sidebar-wrapper has-data-[variant=inset]:bg-sidebar
│                                  flex min-h-svh w-full
│   ├── WorkspaceSidebar          <Sidebar variant="sidebar" collapsible="icon">
│   │   width: --sidebar-width = 16rem  | collapsed: --sidebar-width-icon = 3rem
│   │   ├── SidebarHeader → WorkspaceHeader (logo + new-chat link)
│   │   ├── SidebarContent
│   │   │   ├── WorkspaceNavChatList
│   │   │   ├── WorkspaceChannelsList
│   │   │   └── RecentChatList
│   │   ├── SidebarFooter → WorkspaceNavMenu
│   │   └── SidebarRail (drag-handle rail)
│   └── SidebarInset              (flex-1, ml=sidebar-width, transitions on collapse)
│       └── WorkspaceHeader       h-16, group-has-data-[collapsible=icon]/sidebar-wrapper:h-12
│           ├── Breadcrumb
│           └── github icon
│       └── WorkspaceBody
│           main > div flex h-full w-full flex-col items-center
│               └── [route content]
```

**The actual chat "canvas"** (`src/app/workspace/chats/[thread_id]/page.tsx`):
The chat page is a **two-column flex inside `<ChatBox>`**:

```jsx
<div className="relative flex size-full min-h-0 justify-between">
  <header className="absolute top-0 right-0 left-0 z-30 flex h-12 …">
    <SidebarTrigger className="md:hidden" />
    … agent badge (only on /agents/[agent_name]/…) …
    <ThreadTitle />
    <div className="flex shrink-0 items-center sm:mr-4">
      <NewChatButton />
      <TokenUsageIndicator />
      <ExportTrigger />
      <ArtifactTrigger />
    </div>
  </header>

  <main className="flex min-h-0 max-w-full grow flex-col">
    <div className="flex min-h-0 flex-1 justify-center">
      <MessageList className="size-full …" />
    </div>
    <div className="right-0 bottom-0 left-0 z-30 flex justify-center px-3 sm:px-4">
      <div className="relative w-full max-w-(--container-width-md)">
        {" "}
        // = 204 * 0.25rem = 51rem ≈ 816px
        <TodoList />
        <InputBox />
      </div>
    </div>
  </main>
</div>
```

**The only real panel split** — `chat-box.tsx`:

```tsx
<ResizablePanelGroup
  id={…}
  orientation="horizontal"
  defaultLayout={{ chat: 100, artifacts: 0 }}
  groupRef={layoutRef}
>
  <ResizablePanel className="relative" defaultSize={100} id="chat">   {/* ← chat surface */}
    {children}
  </ResizablePanel>
  <ResizableHandle … />
  <ResizablePanel
    className={cn("transition-all …", !artifactsOpen && "opacity-0")}
    id="artifacts"                                                    {/* ← artifact surface */}
  >
    {/* ArtifactFileDetail or ArtifactFileList */}
  </ResizablePanel>
</ResizablePanelGroup>
```

Two presets:

```ts
const CLOSE_MODE = { chat: 100, artifacts: 0 };
const OPEN_MODE = { chat: 60, artifacts: 40 };
```

### 11.3 WebSocket / SSE connections to backend

- **No direct** `EventSource`, `WebSocket`, or `text/event-stream` consumers in the codebase. All real-time streaming goes through the LangGraph SDK.
- **LangGraph SDK streaming** via `useStream` from `@langchain/langgraph-sdk/react`, configured once in `src/core/threads/hooks.ts`.
- **Pollers / intervals** (also "long-lived"):
  - `gateway-offline-banner.tsx` — every 10 s
  - `AuthProvider.tsx` — `visibilitychange` → throttled to once/60s
  - `connect-poll.ts` — every 2 s up to `expires_in` (default 600 s)
  - `message-list.tsx` (LoadMoreHistoryIndicator) — IntersectionObserver, 1.2 s throttle
  - `recent-chat-list.tsx` — sentinel IntersectionObserver
  - `threads/hooks.ts` + `context.ts` — LangGraph SDK reconnects on mount

### 11.4 API calls to FastAPI gateway and LangGraph

**Transport conventions**

- `getBackendBaseURL()` → `env.NEXT_PUBLIC_BACKEND_BASE_URL` (defaults to empty string; relative to `window.location.origin`)
- `getLangGraphBaseURL()` → `env.NEXT_PUBLIC_LANGGRAPH_BASE_URL`, or `<origin>/api/langgraph` (default) / `<origin>/mock/api` (when `?mock=true`)
- Wrapper `fetch()` in `src/core/api/fetcher.ts` adds:
  - `credentials: "include"` (access_token cookie is HttpOnly)
  - `X-CSRF-Token: <csrf_token cookie>` for `POST|PUT|DELETE|PATCH`
  - Auto-redirect to `/login?...` on `401`
- LangGraph SDK uses an `onRequest` hook in `api-client.ts` to mirror the CSRF contract on `client.runs.*`
- `src/app/api/memory/**` is a server-side proxy to `process.env.NEXT_PUBLIC_BACKEND_BASE_URL` (default `http://127.0.0.1:8001`)

**FastAPI gateway endpoints (and where they're called)**

| Method | Path (relative to `getBackendBaseURL()`) | Caller(s) |
|---|---|---|
| GET | `/api/models` | `core/models/api.ts` → `useModels` |
| GET | `/api/agents` | `core/agents/api.ts` → `useAgents` |
| POST | `/api/agents` | `core/agents/api.ts` → `useCreateAgent` |
| GET | `/api/agents/{name}` | `core/agents/api.ts` → `useAgent` |
| PUT | `/api/agents/{name}` | `core/agents/api.ts` → `useUpdateAgent` |
| DELETE | `/api/agents/{name}` | `core/agents/api.ts` → `useDeleteAgent` |
| GET | `/api/skills` | `core/skills/api.ts` → `loadSkills` |
| POST | `/api/skills/{name}` | `core/skills/api.ts` → `enableSkill` |
| POST | `/api/skills/install` | `core/skills/api.ts` → `installSkill` |
| GET | `/api/memory` | `core/memory/api.ts` → `loadMemory`; also proxied via `src/app/api/memory/route.ts` |
| GET | `/api/mcp/config` | `core/mcp/api.ts` → `loadMCPConfig` |
| PUT | `/api/mcp/config` | `core/mcp/api.ts` → `saveMCPConfig` |
| GET | `/api/channels/providers` | `core/channels/api.ts` → `listChannelProviders` |
| POST | `/api/channels/{provider}/connect` | `core/channels/api.ts` → `connectChannelProvider` |
| GET | `/api/threads/{threadId}/token-usage` | `core/threads/api.ts` → `useThreadTokenUsage` |
| POST | `/api/threads/{threadId}/runs/{runId}/feedback` | `core/api/feedback.ts` |

**LangGraph SDK endpoints** (used by `useStream` + `api-client.ts`)

| SDK method | Where |
|---|---|
| `client.runs.stream(threadId, "lead_agent", payload)` | `useStream({ assistantId: "lead_agent", … })` |
| `client.runs.joinStream(threadId, runId)` | Reconnect logic in `api-client.ts` patch |
| `client.threads.getState<AgentThreadState>(threadId)` | `recent-chat-list.tsx` (`handleExport`) |
| `client.threads.update(threadId, { metadata })` | `useStream.onCreated` (writes `agent_name`) |
| `client.threads.search(params, …)` | `useInfiniteThreads` / `useThreads` — uses `useInfiniteQuery` |
| `client.threads.create({ … })` | Implicit via `client.runs.stream` with new threadId |
| `client.threads.delete(threadId)` | `useDeleteThread` (via `core/threads/hooks.ts`) |

### 11.5 Files that would need changing for a 4-panel layout

**Core layout containers (must change):**

- `src/components/workspace/chats/chat-box.tsx` — Replace 2-panel `ResizablePanelGroup` with 4-panel grid (or nested groups). Add new `OPEN_MODE`/`CLOSE_MODE` presets.
- `src/app/workspace/chats/[thread_id]/providers.tsx` — Possibly introduce a fourth context (e.g. `AgentToolsProvider`).
- `src/app/workspace/agents/[agent_name]/chats/[thread_id]/layout.tsx` — Mirror the providers change.
- `src/app/workspace/chats/[thread_id]/page.tsx` — Wire new header triggers, pass `selectedTool` state into ChatBox.
- `src/components/workspace/todo-list.tsx` — Currently renders as overlay over InputBox. For 4-panel grid, lift into bottom panel; remove absolute-positioning classes.

**New panel content (new components to create):**

- `src/components/workspace/agent-tools/agent-tools-panel.tsx` — Bottom panel shell
- `src/components/workspace/agent-tools/agent-tools-tool-call-list.tsx` — Per-thread tool-call registry / status / debug console
- `src/components/workspace/settings/tools-settings-section.tsx` — Toggle "Show agent tools panel", default size, start state

**Components inside ChatBox needing container-query updates:**

- `artifacts/context.tsx`, `artifact-trigger.tsx`, `artifact-file-list.tsx`, `artifact-file-detail.tsx` — Add `bottomPanelOpen` state
- `input-box.tsx` — If InputBox becomes sibling of new panel rather than footer dock, move dock logic out of `page.tsx`
- `message-list.tsx` — `MESSAGE_LIST_DEFAULT_PADDING_BOTTOM` may need new value if bottom panel collapsible
- `todo-list.tsx` — Convert from absolute-positioned overlay to panel-resident component
- `gateway-offline-banner.tsx` — Verify z-index above 4 panels

**Hooks and contexts (state plumbing):**

- `messages/context.ts` — Extend `ThreadContextType` only if new panel needs thread-scoped state
- `artifacts/context.tsx` — Lift panel-state into a more general `PanelsContext` (artifacts + new panel)
- `hooks/use-global-shortcuts.ts` / `command-palette.tsx` — Add shortcut for toggling new panel

**I18N strings to add:**

- `src/core/i18n/locales/en-US.ts` — New panel title, toggle, tool-call labels
- `src/core/i18n/locales/zh-CN.ts` — Mirror in zh-CN
- `src/core/i18n/locales/types.ts` — Update dictionary types

**Settings dialog (if panel is toggleable):**

- `settings/index.ts` — Register new section (e.g. `"agent-tools"`)
- `settings/tools-settings-section.tsx` — Toggle "Show agent tools panel", default size, start state
- `core/settings/local.ts` / `store.ts` / `index.ts` — Persist toggles in localStorage (per-thread if desired)

**Backend / API surface (no strict requirement, but if new panel needs data):**

- `GET /api/threads/{threadId}/subagents` (subtask registry)
- `GET /api/threads/{threadId}/tool-calls`
- `GET /api/threads/{threadId}/todos` (currently derived from `useStream.onUpdateEvent`; if persisted, expose them)

---

## 12. Pipeline verification

### 12.1 What "works in cohesion" means here

A user prompt → working code delivery requires:

1. ✅ Prompt validated + auth checked
2. ✅ LLM responds with plan
3. ✅ Tool calls execute in correct thread sandbox
4. ✅ StreamBridge emits events at each step
5. ✅ Frontend renders chain-of-thought in real time
6. ✅ Browser check auto-runs after present_files
7. ⚠️ Verify result shown to user (VerifyResultPill works; LlmErrorBadge works)
8. ❌ Browser health invisible to user during degradation
9. ❌ Skills/tools loaded not surfaced as status

Steps 1-7 verified working. Steps 8-9 are the gaps this sprint addresses.

### 12.2 Test order (dependency-ordered)

```
1. backend pytest (smallest scope)
   → 264 baseline → 483 v7
2. frontend vitest
   → 339 unchanged
3. live smoke:
   → /health 200
   → /api/health/browser 200 (NEW)
   → /api/metrics 200 (NEW)
   → /workspace 307 redirect
```

---

## 13. Final state — single-page summary

| Metric | Value |
|---|---|
| Backend files | 252 |
| Backend LOC | ~32k |
| Frontend files | ~120 |
| Frontend LOC | ~12k (incl. node_modules) |
| Backend tests | 483 (all green) |
| Frontend tests | 339 (all green) |
| Tags on origin | 14 v7 + fork-v7 |
| Live services | 4 (gateway, frontend, nginx, pm2 wrapper) |
| Browser tool actions | 5 (navigate, click, input, eval, screenshot) |
| Shell tool actions | 5 (session, view, wait, write, kill) |
| Skills system | Public + custom, parser + storage + security scanner |
| Subagents | 2 builtins (bash, general-purpose) |
| Middlewares | 10 (one responsibility each) |
| LLM providers | OpenAI + compatible (Together, Groq, custom) |
| MCP servers | dynamic, OAuth support |
| Channels | 6 (Telegram, Slack, Discord, Feishu, DingTalk, WeChat/WeCom) |
| Tools (builtin) | 26 |
| Live observability | Logs (JSON), Prometheus metrics, /api/health/browser |

---

## 14. What's shipping in this round (v7.1)

1. ✅ nginx recovered (this very minute)
2. ⏳ Audit document (this file)
3. ⏳ `/api/runtime/capabilities` endpoint — skills, tools, hooks, subagents, circuit_states
4. ⏳ `<RuntimeCapabilitiesBar />` — visible status bar at top of chat
5. ⏳ `<BrowserHealthIndicator />` — green/amber/red dot
6. ⏳ `<CircuitBreakerBadge />` — per-thread breakdown
7. ⏳ Full regression green
8. ⏳ Tag + push + zip
