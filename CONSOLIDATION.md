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
| 3   | Introduce typed platform services                                         | C1    | pending  | 0%         |
| 4   | Centralize run state                                                     | C2    | pending  | 0%         |
| 5   | Standardize lifecycle management                                         | C2    | pending  | 0%         |
| 6   | Strengthen self-healing                                                  | C3    | pending  | 0%         |
| 7   | Unify observability                                                      | C0    | partial  | 40%        |
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

## Next phases (preview)

| Phase | Title                                                | Depends on  |
| ----- | ---------------------------------------------------- | ----------- |
| C3    | Unified lifecycle state machine                       | C2          |
| C4    | Self-healing + RecoveryService                        | C2, C3      |
| C5    | Tool protocol standardization                         | C2          |
| C6    | Workspace/Repository abstraction                      | C2          |
| C7    | Deployment, HA, production hardening                  | C3–C6       |
| C8    | Performance optimization and scaling                  | C7          |
| C9    | Product features and extensibility                    | C8          |
