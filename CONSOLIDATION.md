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

## Phase C0 — Foundation (in progress)

**Objective.** Cross-process correlation. Integration test foundation.
Consolidation tracking. CI guardrails. No user-facing changes.

### C0.1 — Backend `RunRecord.correlation_id`

- **Status:** complete.
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
- **Migration notes:** legacy rows hydrated with `correlation_id=""` —
  consumers must fall back to `run_id` when the field is empty.

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

### Validation summary for Phase C0

| Step                         | Result   |
| ---------------------------- | -------- |
| Frontend typecheck            | pass     |
| Frontend unit tests (delta)   | +8 pass  |
| Backend unit tests (delta)    | +18 pass |
| Backend targeted streaming    | 29 pass  |
| Cross-ref check               | pass     |

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

## Next phases (preview)

| Phase | Title                                                | Depends on  |
| ----- | ---------------------------------------------------- | ----------- |
| C1    | Typed service layer                                   | C0          |
| C2    | Centralized run state + lifecycle                    | C1          |
| C3    | Self-healing + unified observability                  | C2          |
| C4    | Tool interface standardization                         | C1, C2      |
| C5    | Replaceability + reliability                         | C1–C4       |
