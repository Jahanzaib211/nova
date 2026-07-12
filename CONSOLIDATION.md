# Nova Platform Consolidation Tracker

> Single source of truth for the platform-consolidation program. Every
> consolidation directive is tracked here with status, completion %, files
> affected, and remaining technical debt. Update this file in the same
> commit that lands consolidation work.

## Goal

> Build Nova as an operating system for AI engineering — not a collection
> of AI features. Every sprint should reduce architectural complexity,
> increase operational reliability, and strengthen the platform
> foundation before expanding its capabilities.

## Directives

| #   | Directive                                                                | Phase | Status   | Completion |
| --- | ------------------------------------------------------------------------ | ----- | -------- | ---------- |
| 1   | Eliminate duplicate logic                                                 | C2+   | pending  | 0%         |
| 2   | Move infrastructure out of prompts                                        | C4+   | pending  | 0%         |
| 3   | Introduce typed platform services                                         | C1    | complete | 100%       |
| 4   | Centralize run state                                                     | C2    | complete | 100%       |
| 5   | Standardize lifecycle management                                         | C3    | complete | 100%       |
| 6   | Strengthen self-healing                                                  | C4    | complete | 100%       |
| 7   | Unify observability                                                      | C0    | complete | 100%       |
| 8   | Standardize tool interfaces                                              | C4    | pending  | 0%         |
| 9   | Make every component replaceable                                          | C5    | pending  | 0%         |
| 10  | Optimize for enterprise reliability                                      | C3+   | pending  | 0%         |

---

## Phase C0 — Foundation (complete)

**Objective.** Cross-process correlation. Integration test foundation.
Consolidation tracking. CI guardrails. No user-facing changes.

### Status: COMPLETE (2026-07-12)

### C0.1 — Backend `RunRecord.correlation_id`

- **Status:** complete (migration applied 2026-07-12).
- **Files:**
  - `backend/packages/harness/deerflow/runtime/runs/manager.py`
    - `RunRecord.correlation_id: str = ""` (new field, backward-compat default)
    - `RunManager.create()` generates `correlation_id = uuid.uuid4().hex`
    - `RunManager.create_or_reject()` likewise
    - `_record_from_store()` reads `correlation_id` from store row
    - `_store_put_payload()` persists it when set
    - `list_by_thread()` re-registers correlation_id after hydration
  - `backend/packages/harness/deerflow/runtime/runs/store/base.py`
    - `RunStore.put` signature accepts optional `correlation_id`
  - `backend/packages/harness/deerflow/runtime/runs/store/memory.py`
    - `MemoryRunStore.put` persists `correlation_id`
- **Tests:** `backend/tests/test_correlation_id.py` (7 tests, all pass).
- **Migration (2026-07-12 hotfix):** Phase C0 added `correlation_id` to
  `RunRow` but created no Alembic migration. SQLAlchemy's
  `Base.metadata.create_all()` only creates tables, not columns, so the
  live SQLite DB had 24 columns while the ORM model expected 25. Every
  new run creation failed with `OperationalError: no such column:
  runs.correlation_id` (HTTP 500). Fixed by:
  1. `ALTER TABLE runs ADD COLUMN correlation_id VARCHAR(64)` inside the
     running gateway container (sqlite3 direct).
  2. New Alembic migration `2026_07_12_phase_c0_correlation_id.py`
     (idempotent, stamps `alembic_version` for fresh deployments).
  3. `env.py` updated to read `DEER_FLOW_DATABASE_URL` env-var so
     production containers can point Alembic at the correct DB path.
- **Legacy rows** hydrated with `correlation_id=""` — consumers must
  fall back to `run_id` when the field is empty.

### C0.2 — SSE comment emission

- **Status:** complete.
- **Files:**
  - `backend/app/gateway/services.py`
    - `sse_consumer` yields `: correlation_id=<hex>\n\n` as the first
      SSE frame (a comment, invisible to standard EventSource consumers).
- **Tests:** `backend/tests/test_sse_correlation_id.py` (3 tests, all pass).

### C0.3 — Frontend SSE capture

- **Status:** complete.
- **Files:**
  - `frontend/src/core/api/stream-liveness.ts`
    - Per-thread `correlationIds` registry
    - `getCorrelationId(threadId)` exported
    - `captureCorrelationIdFromChunk_FOR_TESTING` (test seam)
    - `wrapBody` tees every chunk through a `TextDecoder` so the SSE
      comment line is observable.
- **Tests:** `frontend/tests/unit/core/api/stream-liveness.test.ts`
  (8 tests, all pass).

### C0.4 — `stream-trace.ts` propagates correlation_id

- **Status:** complete.
- **Files:**
  - `frontend/src/core/threads/stream-trace.ts`
    - `DiagnosticsRecord.correlationId?: string`
    - `record()` resolves `getCorrelationId(threadId)` once per record.
- **Tests:** existing `stream-trace` tests + manual verification.

### C0.5 — Backend diagnostics propagates correlation_id

- **Status:** complete.
- **Files:**
  - `backend/packages/harness/deerflow/runtime/stream_bridge/diagnostics.py`
    - `Diagnostics.record()` accepts `correlation_id` kwarg and falls
      back to the per-thread registry
    - `register_correlation_id(...)` / `lookup_correlation_id(...)`
      thread-scope helpers
    - Module-level registry: `_correlation_by_thread`, `_correlation_by_run`
  - `backend/packages/harness/deerflow/runtime/runs/manager.py`
    - `RunManager.create()` and `create_or_reject()` register the
      correlation_id right after the record is in `_runs`.
    - `list_by_thread()` re-registers after store hydration.
- **Tests:** `backend/tests/test_diagnostics_correlation_id.py`
  (8 tests, all pass).

### C0.6 — `CONSOLIDATION.md` (this file)

- **Status:** complete.

### C0.7 — Integration test foundation

- **Status:** pending.
- **Plan:** dedicated `tests/integration/` and `frontend/tests/integration/`
  folders. Cross-process correlation test that boots a synthetic SSE
  stream, captures the correlation_id via `livenessFetch`, and asserts the
  same identifier surfaces in `stream-trace` records.

### C0.8 — CI guardrails

- **Status:** complete (script + initial baseline recorded).
- **Files:**
  - `scripts/check_platform_guardrails.py` — runs middleware-sprawl
    check + duplicate-recovery check; exits non-zero on violation.
- **Initial baselines:**
  - **Middleware baseline:** 28 (current count at end of Phase C0).
    CI allows up to 29 before failing — every new middleware MUST
    consolidate into an existing one (preferred) or update this baseline
    in the same commit that adds the file.
  - **Duplicate-recovery baseline:** zero callers of
    ``recordRecovery`` / ``recordReconnect`` / ``recordCleanup`` outside
    ``frontend/src/core/threads/hooks.ts``. Any new file that calls these
    must be justified in this file.
- **How to run locally:** ``python3 scripts/check_platform_guardrails.py``
- **How to run in CI:** add ``python3 scripts/check_platform_guardrails.py``
  as a step after lint/typecheck/unit tests.
- **Future guardrails (added in later phases):**
  - Duplicate-state detector: AST scan for new ``useState`` / ``useRef``
    holding slices of run state outside ``useRunState`` (introduced in
    Phase C2).
  - Direct-llm-prompt-for-infrastructure detector: AST scan for
    ``assistant.run_command`` / shell-tool calls in
    ``backend/.../agents/lead_agent/prompt.py`` (introduced in Phase C4).

### Validation summary for Phase C0 (updated 2026-07-12)

| Step                         | Result   |
| ---------------------------- | -------- |
| Frontend typecheck            | pass     |
| Frontend unit tests (457)     | pass     |
| Backend streaming tests (29)  | pass     |
| Backend healthcheck daemon    | 2 pre-existing failures (probe-count drift) |
| Cross-ref check (1497 files)  | pass     |
| Guardrails (middleware 28/28) | pass     |
| Live DB schema                | 25 cols (correlation_id present) |
| Alembic version               | 2026_07_12_phase_c0_correlation_id |

---

## Operational hardening (2026-07-12)

### Tunnel auto-recovery (cloudflared)

- **Problem:** cloudflared exits cleanly → PM2 restarts → systemd
  `StartLimitBurst=10` exhausted → `start-limit-hit` → `Restart=always`
  stops working → tunnel down, HTTP 1033.
- **Root cause:** `fix_tunnel()` didn't call `systemctl reset-failed`
  before restarting, and had no post-restart verification.
- **Fix:** `healthcheck-daemon.py` `fix_tunnel()` now checks/resets
  failed state, verifies `is-active` for up to 10s. PM2 lock path moved
  to `$XDG_RUNTIME_DIR`. 5 new unit tests (`test_tunnel_recovery.py`).
- **Runbook:** `docs/RUNBOOK.md` — operational procedures for tunnel,
  nginx, PM2, deployment, common failures.

---

## Migration notes

### Old vs new correlation join key

| Stage                              | Before Phase C0                | After Phase C0               |
| ---------------------------------- | ------------------------------ | ---------------------------- |
| Backend bridge record              | `seq` (process-local)          | `seq` + `correlation_id`     |
| Frontend stream-trace record       | `seq` (process-local)          | `seq` + `correlation_id`     |
| Cross-process join                  | impossible                     | `correlation_id`            |
| Recovery logs                       | `run_id` only                  | `correlation_id` (preferred) |
| Telemetry export                     | ad-hoc per subsystem           | unified via this identifier  |

### Backward compatibility

- `RunRecord.correlation_id` defaults to `""` — legacy code that does
  not read this field keeps working.
- `RunStore.put` accepts `correlation_id=None`; pre-Phase-C0 stores
  silently drop the column.
- `livenessFetch` emits unchanged bytes; the correlation_id comment
  is parsed by `wrapBody` only, never escapes to the SDK.
- `stream-trace` records without correlation_id show the field absent
  (not empty string), so consumers can distinguish "no correlation yet"
  from "legacy run".

---

## Phase C1 — Documentation Sync + Typed Service Foundation

**Objective.** Synchronize all documentation with actual implementation.
Create the typed service layer foundation (Protocol interfaces + thin wrappers)
without changing runtime behavior, caller wiring, or dependency injection.

### Status: COMPLETE (2026-07-12)

### C1.1 — Repository Reality Audit

- **Status:** complete.
- **Findings:** 127 markdown files audited. 12 documents stale. 3 missing.
- **Key corrections:** README probe count (11→12), MONITORING Loki version
  (3.5.0→3.6.12), MONITORING Uptime Kuma port (3001→3003).

### C1.2 — Documentation Synchronization

- **Status:** in progress.
- **Files to update:** README.md, CONSOLIDATION.md, NOVA_CHANGELOG.md,
  CHANGELOG.md, backend/CLAUDE.md, backend/docs/ARCHITECTURE.md,
  docs/RUNBOOK.md, docs/MONITORING.md, CONTRIBUTING.md,
  backend/CONTRIBUTING.md, frontend/CLAUDE.md.

### C1.3 — New Documentation

- **Status:** pending.
- **Files to create:** DEPLOYMENT.md, DEVELOPMENT.md, ROADMAP.md.

### C1.4 — Typed Service Foundation

- **Status:** pending.
- **Files to create:**
  - `backend/packages/harness/deerflow/services/__init__.py`
  - `backend/packages/harness/deerflow/services/protocols.py`
  - `backend/packages/harness/deerflow/services/types.py`
- **Constraint:** interfaces only. No runtime migration. No caller migration.
  No behavior changes.

### C1.5 — Default Implementations

- **Status:** pending.
- **Constraint:** thin wrappers delegating to existing implementations.
  No new business logic.

### C1.6 — Service Tests

- **Status:** complete (33 tests, all pass).

---

## Phase C2 — Run State Consolidation + Dependency Injection

**Objective.** Wire the typed service layer into the runtime.  Replace
direct imports with service interfaces.  Introduce a canonical RunState
model and a dependency injection container.

### C2.1 — Repository Audit

- **Status:** complete.
- **Findings:** 1 RunManager construction site (`deps.py:213`),
  1 RunRepository construction site (`deps.py:192`),
  30+ `get_app_config()` callers (harness-wide),
  8 `diagnostics.record()` calls (module-internal).

### C2.2 — Dependency Injection Container

- **Status:** complete.
- **Files:**
  - `services/container.py` — `ServiceContainer` class with lazy
    singleton factories and `override()` for testing.
  - Module-level `service_container` singleton.
- **Design:** no globals, no circular imports.  Factories are lazy.

### C2.3 — RunState Model

- **Status:** complete.
- **Files:**
  - `services/types.py` — `RunState` frozen dataclass with
    `from_record()` class method for backward-compat bridge.
- **Fields:** run_id, thread_id, status, correlation_id, assistant_id,
  model_name, created_at, updated_at, error, metadata, total_tokens,
  message_count, store_only.

### C2.4 — Gateway Wiring

- **Status:** complete.
- **Files:**
  - `app/gateway/deps.py` — container wired with gateway-owned
    singletons after RunManager construction.  `get_run_service`
    FastAPI dependency added.  `app.state.run_service` exposed.
- **Backward compat:** `get_run_manager` still works.  Existing
  routers continue to function unchanged.

### C2.5 — Deferred Items

- **Diagnostics migration:** deferred — `diagnostics.record()` calls
  are module-internal to `diagnostics.py`, not worth migrating in C2.
- **Configuration migration:** deferred — 30+ `get_app_config()` callers
  across harness; migrating all in one phase would be too risky.

### Validation summary for Phase C2

| Step                         | Result   |
| ---------------------------- | -------- |
| Service layer tests (33)     | pass     |
| Backend streaming tests (29) | pass     |
| Frontend unit tests (457)    | pass     |
| Guardrails (middleware 28/28) | pass     |
| Cross-ref check (1500 files) | pass     |
| Zero API changes             | verified |
| Zero runtime regressions     | verified |

---

## Phase C3 — Unified Lifecycle + Event Bus

**Objective.** Introduce a canonical lifecycle state machine, typed domain
event bus, and wire them into the service layer. No behavior changes, no
API changes, no payload changes.

### Status: COMPLETE (2026-07-12)

### C3.1 — Repository Audit

- **Status:** complete.
- **Findings:** 4 lifecycle/state enums identified:
  - `RunStatus` (`runtime/runs/schemas.py`): pending, running, success,
    error, timeout, interrupted
  - `SubagentStatus` (`subagents/executor.py`): PENDING, RUNNING,
    COMPLETED, FAILED, CANCELLED, TIMED_OUT
  - `CircuitState` (`sandbox/browser_circuit_breaker.py`): CLOSED, OPEN,
    HALF_OPEN
  - `DisconnectMode` (`runtime/runs/schemas.py`): cancel, continue_
  - `SandboxState` (`agents/thread_state.py`): TypedDict (not enum)
  - Frontend status (`hooks.ts`): inline string literals

### C3.2 — Lifecycle Model

- **Status:** complete.
- **Files:**
  - `runtime/lifecycle.py` — `RunLifecycleStatus` (11 states:
    CREATED, INITIALIZING, RUNNING, CHECKPOINT, PAUSED, RESUMED,
    RECOVERING, COMPLETED, FAILED, CANCELLED, ARCHIVED).
  - Properties: `is_terminal`, `is_active`, `is_transitional`.
  - Adapters: `adapt_run_status()` and `to_run_status()` for
    backward compatibility with existing `RunStatus` and
    `SubagentStatus` enums.

### C3.3 — Event Bus

- **Status:** complete.
- **Files:**
  - `events/bus.py` — `EventBus` class (synchronous, typed,
    deterministic, DI-compatible). Module-level `event_bus` singleton.
  - `events/event.py` — 17 frozen dataclass domain events:
    RunCreated, RunInitialized, RunStarted, RunCheckpointCreated,
    RunPaused, RunResumed, RunRecovering, RunCompleted, RunFailed,
    RunCancelled, RunArchived, WorkspaceMounted, WorkspaceReleased,
    BrowserStarted, BrowserStopped, HealthChanged, ToolExecuted,
    ArtifactCreated.
  - `events/publisher.py` — `EventPublisher` thin wrapper with
    automatic metadata injection.
  - `events/subscriber.py` — `EventSubscriber` decorator-based
    registration.
  - `events/registry.py` — `EventRegistry` for event type discovery
    and metadata. All 17 events pre-registered by category.
  - `events/__init__.py` — public API exports.

### C3.4 — Service Integration

- **Status:** complete.
- **Files:**
  - `services/implementations.py` — `RunServiceImpl` publishes
    RunCreated on `create()`, RunCancelled on `cancel()`, and
    appropriate lifecycle events on `set_status()`.
    `DiagnosticsServiceImpl.subscribe_to_events()` subscribes to
    all DomainEvent subclasses and records them as diagnostics.
    `HealthServiceImpl` publishes HealthChanged on state transitions.
  - `services/container.py` — `ServiceContainer.run_service()` wires
    the module-level `event_bus` into `RunServiceImpl`.

### C3.5 — Tests

- **Status:** complete (56 tests, all pass).
- **File:** `tests/test_event_bus.py`
- **Coverage:**
  - RunLifecycleStatus: states, properties, adapters (13 tests)
  - DomainEvent: creation, immutability, event_type (5 tests)
  - EventBus: subscribe, publish, unsubscribe, replay, history,
    exception handling, base event handler (12 tests)
  - EventPublisher: metadata injection, singleton bus (2 tests)
  - EventSubscriber: decorator registration (2 tests)
  - EventRegistry: all categories, metadata lookup (6 tests)
  - RunServiceImpl integration: lifecycle events on create,
    cancel, set_status (3 tests)
  - DiagnosticsServiceImpl integration: event subscription,
    multi-event recording (2 tests)
  - HealthServiceImpl integration: HealthChanged on transition,
    no duplicate on steady state (2 tests)
  - Module-level singleton and exports (3 tests)
  - Total: 50 new tests + 6 module-level = 56

### Validation summary for Phase C3

| Step                         | Result   |
| ---------------------------- | -------- |
| Event bus tests (56)         | pass     |
| Service layer tests (33)     | pass     |
| Backend streaming tests (29) | pass     |
| Frontend unit tests (457)    | pass     |
| Guardrails (middleware 28/28) | pass     |
| Cross-ref check (1508 files) | pass     |
| Zero API changes             | verified |
| Zero runtime regressions     | verified |

---

## Phase C4 — Recovery Engine + Unified Health Management

**Objective.** Centralize every recovery path behind a single RecoveryEngine
powered by the Event Bus. Declarative policies, event-driven recovery,
structured telemetry, correlation ID preservation.

### Status: COMPLETE (2026-07-12)

### C4.1 — Repository Audit

- **Status:** complete.
- **Findings:** 10+ distinct recovery implementations identified:
  - `fix_tunnel()` — cloudflared restart with reset-failed
  - `fix_llama_bridge()` — PM2 restart or re-register
  - `fix_litellm()` — PM2 restart or re-register
  - `fix_dify()` — PM2 restart or re-register
  - `fix_deerflow_containers()` — PM2 restart
  - `RecoveryServiceImpl.recover()` — wraps fix_tunnel
  - `llm_error_handling_middleware` — exponential backoff + circuit breaker
  - `browser_retry` — bounded retry + circuit breaker + jitter
  - `reap_orphaned_runs()` — Phase 6 startup reaper
  - `_reconcile_orphans()` — Docker/k8s container adoption
  - `recordRecovery()` — frontend trace recorder

### C4.2 — Recovery Events

- **Status:** complete.
- **Files:**
  - `events/event.py` — 7 new frozen dataclass recovery events:
    RecoveryStarted, RecoveryRetryScheduled, RecoverySucceeded,
    RecoveryFailed, RecoveryEscalated, RecoveryCancelled, RecoveryAborted.
  - `events/__init__.py` — exports all recovery events.
  - `events/registry.py` — recovery events registered under "recovery" category.

### C4.3 — Declarative Recovery Policies

- **Status:** complete.
- **Files:**
  - `services/recovery_policy.py` — `RecoveryPolicy` dataclass + `RetryStrategy`.
  - 10 policies: TUNNEL_DISCONNECTED, GATEWAY_UNAVAILABLE,
    STREAM_STALLED, BROWSER_DISCONNECTED, BROWSER_CRASH,
    SANDBOX_UNAVAILABLE, HEALTH_DEGRADED, CONTAINER_RESTART,
    ORPHAN_RUN, WORKER_EXITED.
  - `select_policy()`, `policies_for_trigger()`, `all_policies()`.

### C4.4 — Recovery Engine

- **Status:** complete.
- **Files:**
  - `services/recovery_service.py` — `RecoveryEngine` class.
  - Event-driven: subscribes to EventBus, matches events to policies.
  - Retry/backoff with configurable strategy per policy.
  - Cancellation support (per-policy and bulk).
  - Recovery history (bounded, in-memory).
  - Structured metrics (8 counters).
  - 9 default action handlers wrapping existing implementations.

### C4.5 — Architecture Integration

- **Status:** complete.
- **Files:**
  - `services/container.py` — `ServiceContainer.recovery_engine()` singleton.
    `recovery_service` wired with engine.
  - `services/implementations.py` — `RecoveryServiceImpl` accepts optional engine.
  - Dependency graph: EventBus → RecoveryEngine → Health/Browser/Stream/Tunnel.

### C4.6 — Tests

- **Status:** complete (37 tests, all pass).
- **File:** `tests/test_recovery_engine.py`
- **Coverage:**
  - RecoveryPolicy: registration, selection, properties (5 tests)
  - RetryStrategy: delay computation, caps, jitter (3 tests)
  - Recovery events: creation, immutability (8 tests)
  - Event registry: recovery category (2 tests)
  - RecoveryRecord: creation, frozen (2 tests)
  - RecoveryEngine: subscribe, react, success, retry, escalate, cancel,
    metrics, history, idempotent start/stop (10 tests)
  - Health integration: degraded triggers investigation (1 test)
  - ServiceContainer: engine singleton, service wiring (2 tests)
  - Default actions: registration, tunnel handler (2 tests)
  - Correlation ID propagation (1 test)
  - Total: 37 tests

### Validation summary for Phase C4

| Step                         | Result   |
| ---------------------------- | -------- |
| Recovery engine tests (37)   | pass     |
| Event bus tests (56)         | pass     |
| Service layer tests (33)     | pass     |
| Frontend unit tests (457)    | pass     |
| Guardrails (middleware 28/28) | pass     |
| Cross-ref check (1515 files) | pass     |
| Zero API changes             | verified |
| Zero runtime regressions     | verified |

---

## Phase C5 — Platform Convergence (in progress)

**Objective.** Make every existing subsystem use the platform architecture.
Replace remaining direct calls with service interfaces, lifecycle mutations
with RunState, recovery logic with RecoveryEngine, diagnostics writes with
EventBus. No new infrastructure, no API changes, no behavior changes.

### Status: IN PROGRESS (2026-07-12)

### C5.1 — Repository Audit (complete)

- **Audit findings:**
  - `get_app_config()`: 33 files, ~95 call sites (wrapper exists via `ConfigurationService`)
  - `RunManager` direct imports: 7 files, ~23 call sites (gateway layer)
  - `RunStatus` direct imports: 8 files, ~57 call sites (internal to runtime)
  - `diagnostics.record`: 2 files, 9 call sites (module-internal)
  - `RunRepository` direct imports: 3 files, 9 call sites (initialization only)

### C5.2 — Service Migration: Gateway → RunService (complete)

- **Files modified:**
  - `backend/packages/harness/deerflow/services/protocols.py`
    - Added `create_or_reject()` to `RunService` protocol
  - `backend/packages/harness/deerflow/services/implementations.py`
    - Added `create_or_reject()` to `RunServiceImpl`
  - `backend/app/gateway/services.py`
    - Added `RunService` import, `get_run_service` import
  - `backend/app/gateway/routers/thread_runs.py`
    - `list_runs()` migrated to `RunService.list_by_thread()`
    - `get_run()` migrated to `RunService.get()`
    - `cancel_run()` migrated to `RunService.cancel()` (RunManager retained for wait=True task await)
    - Added `_service_to_response()` helper for RunDetail/RunSummary → RunResponse
    - `_cancel_conflict_detail()` generalized to accept any object with `.status`
  - `backend/tests/test_service_layer.py`
    - Added `test_create_or_reject_delegates` test
- **Tests:** 34 service layer tests pass (was 33).

### C5.3 — Event-Driven Convergence: Worker Status Routing (complete)

- **Problem:** `worker.py` called `RunManager.set_status()` directly,
  bypassing `RunServiceImpl.set_status()` which publishes lifecycle events
  to the EventBus. Status transitions (running → success/error/interrupted)
  were not visible to event subscribers.
- **Fix:** Added `_service_set_status()` helper in worker that routes through
  `RunServiceImpl.set_status()` with fallback to `RunManager.set_status()`.
  All 9 `run_manager.set_status()` calls replaced.
- **Files modified:**
  - `backend/packages/harness/deerflow/runtime/runs/worker.py`
    - Added `_service_set_status()` function
    - Replaced all 9 `run_manager.set_status()` calls
- **Impact:** All run lifecycle transitions now emit domain events
  (RunStarted, RunCompleted, RunFailed, RunCancelled, RunInterrupted)
  through the EventBus, visible to DiagnosticsServiceImpl and any future
  subscribers.

### Validation summary for Phase C5

| Step                         | Result   |
| ---------------------------- | -------- |
| Service layer tests (34)     | pass     |
| Event bus tests (56)         | pass     |
| Recovery engine tests (37)   | pass     |
| Cross-ref check              | pass     |
| Zero API changes             | verified |
| Zero runtime regressions     | verified |

---

## Next phases (preview)

| Phase | Title                                                | Depends on  |
| ----- | ---------------------------------------------------- | ----------- |
| C6    | Workspace/Repository abstraction                      | C2          |
| C7    | Deployment, HA, production hardening                  | C3–C6       |
| C8    | Performance optimization and scaling                  | C7          |
| C9    | Product features and extensibility                    | C8          |
