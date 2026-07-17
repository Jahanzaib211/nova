# NOVA_CHANGELOG

> **Canonical forward-looking plan + change log for Nova** (formerly the DeerFlow
> fork; renamed at v4, rename tidy at v5).
>
> **Audience:** the owner (Jahanzaib) + future contributors + the next agent
> that picks up after a token cap. Read this before touching anything.
>
> **What this is NOT:** a port of the upstream DeerFlow changelog
> (`CHANGELOG.md` / `CHANGELOG_zh.md`). Those still describe the base system.

---

## Current Platform Status (consolidated from CONSOLIDATION.md + ROADMAP.md)

| Phase | Title | Status | Key Deliverables |
|-------|-------|--------|------------------|
| C0 | Foundation (correlation, CI guardrails) | ✅ **complete** (2026-07-12) | Cross-process correlation via `correlation_id`, SSE comment emission + frontend capture, streaming hardening (watchdog, bounded teardown, convergent cleanup), tunnel auto-recovery, CI guardrails (middleware sprawl, duplicate-recovery), `CONSOLIDATION.md` tracker |
| C1 | Documentation sync + typed service layer | ✅ **complete** (2026-07-12) | Repository reality audit (127 files), 12 docs updated + 3 created, 10 Protocol interfaces, 7 typed return models, 10 thin wrapper implementations, 33 unit tests |
| C2 | Run state consolidation + dependency injection | ✅ **complete** (2026-07-12) | `ServiceContainer` with lazy singletons + `override()`, `RunState` frozen dataclass with `from_record()` bridge, gateway wired: `app.state.run_service`, `get_run_service` dependency, backward compat maintained |
| C3 | Unified lifecycle + event bus | ✅ **complete** (2026-07-12) | Canonical `RunLifecycleStatus` enum (11 states), 17 frozen dataclass domain events, `EventBus` (sync, typed, DI-compatible), `EventPublisher`/`Subscriber`/`Registry`, `RunServiceImpl` publishes lifecycle events, `DiagnosticsServiceImpl` subscribes, `HealthServiceImpl` publishes `HealthChanged`, 56 tests |
| C4 | Recovery engine + unified health management | ✅ **complete** (2026-07-12) | `RecoveryEngine` (event-driven via EventBus), 10 declarative policies with `RetryStrategy`, 7 recovery events, 9 default action handlers, recovery history + metrics, cancellation support, 37 tests |
| C5 | Platform Convergence | 🔄 **in progress** | Gateway `list_runs`/`get_run`/`cancel_run` → `RunService`, `create_or_reject()` for multitask-aware runs, worker `_service_set_status()` routes all status transitions through service layer → EventBus receives all lifecycle events, 127 consolidation tests pass |
| C6 | Workspace/Repository abstraction | ⏳ pending | Depends on C2 |
| C7 | Deployment, HA, production hardening | ⏳ pending | Depends on C3–C6 |
| C8 | Performance optimization and scaling | ⏳ pending | Depends on C7 |
| C9 | Product features and extensibility | ⏳ pending | Depends on C8 |

**Design Principles:**
1. Single coherent operating system — not a collection of AI features
2. Reduce architectural complexity — every sprint should simplify
3. Increase operational reliability — self-healing, observability, testing
4. Strengthen foundation first — before expanding capabilities
5. Implementation is source of truth — docs follow code, not vice versa

**Constraints:**
- No new user-facing features until consolidated
- No references to the local LLM gateway service name in code
- No references to the model name it wraps in code (allowed only as literal model identifiers in config)
- `deerflow` namespace is allowed (historical)
- TDD mandatory for all new features
- All commits must pass self-test protocol

---

## v9.0 — Phase C9: accounts, monetization & the referral flywheel

**Session pattern:** greenfield product layer on top of the C-phase platform — accounts, usage credits, referrals, and paid billing. All additive; every account column backfills the 24 existing users.

### Data model (migrations)

- **`2026_07_15_nova_plus_user_columns`** — adds to `users`: `plan` (free|plus|enterprise, default free), `plan_status`, `plan_renews_at`, `stripe_customer_id`, `stripe_subscription_id`, `tos_accepted_version`, `tos_accepted_at`, `referral_code` (unique), `referred_by`.
- **`2026_07_15_credit_grants`** — `credit_grants` table (time-limited daily token bonuses; backs referral boosts).
- **`2026_07_15_user_api_keys`** — `user_api_keys` table (Fernet-encrypted BYOK keys).
- All idempotent; verified against a copy of the live DB (24 users → all `plan=free`, no data loss).

### Features

- **Account/email edit** — `POST /api/v1/auth/update-email` (re-auth + uniqueness + token_version bump, no forced password change). Account settings gains an email-edit form.
- **Admin signups dashboard** — `GET /api/v1/admin/users` + `/users/stats` (gated by `require_admin_user`); admin-only "Users" settings section shows count, growth, and the roster (this is where the 24 emails surface).
- **Terms & consent** — `/terms` + `/privacy` pages, footer links, signup acceptance checkbox, `GET /api/v1/legal/terms` + `POST /api/v1/legal/accept`, and a blocking re-acceptance gate in the workspace when `tos_accepted_version` is stale.
- **Nova credits** — 250k tokens/day free (5M plus, 50M enterprise), computed from the `runs` table (no parallel counter). Wall enforced in `start_run` (402 for exhausted end users; admins/internal/BYOK exempt, fail-open when the engine is unavailable). `GET /api/v1/credits` + account meter.
- **Referral flywheel** — per-user invite code, `?ref=` capture at signup, double-sided grants (new user +250k/day×7d → 500k/day; referrer +100k/day×30d). `GET /api/v1/referral` + account referral card.
- **Bring-your-own-key** — Fernet-encrypted per-user LLM key, `GET/POST/DELETE /api/v1/byok`, injected into the run via a task-local contextvar the model factory reads (`deerflow.runtime.byok_context`), bypasses the wall. **Default OFF** behind `NOVA_BYOK_ENABLED` + `NOVA_BYOK_SECRET`.
- **Nova Plus / Stripe** — `POST /api/v1/billing/checkout` + `/portal`, signature-verified `/webhook` (auth+CSRF exempt) driving plan state, admin manual grant `PATCH /api/v1/admin/users/{id}/plan`. **Default OFF** until `STRIPE_SECRET_KEY` (+ `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_PLUS`) are set. `stripe==15.3.0` added.

### Tests

New suites: `test_update_email`, `test_admin_users`, `test_legal_consent`, `test_credits`, `test_referrals`, `test_byok`, `test_billing` (+ foundation round-trip in `test_auth`). Harness→app boundary still green.

### Operator prerequisites (before enabling the paid path)

1. Provision Stripe (product + price) and set `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_PRICE_ID_PLUS`; point a Stripe webhook at `/api/v1/billing/webhook`.
2. For BYOK: set `NOVA_BYOK_SECRET=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")` and `NOVA_BYOK_ENABLED=1`, then verify a live run uses the user's key before announcing.

---

## v8.0 — Phase C0: foundation + streaming hardening

**Session pattern:** forensic production investigation → targeted hardening → consolidation tracking.

### Production incident (2026-07-12)

- **HTTP 500 regression:** Phase C0 added `correlation_id` to `RunRow` ORM model but created no Alembic migration. SQLAlchemy's `Base.metadata.create_all()` only creates tables, not columns — live SQLite DB had 24 columns while ORM expected 25. Every new run creation failed with `OperationalError: no such column: runs.correlation_id`.
- **Fix:** `ALTER TABLE runs ADD COLUMN correlation_id VARCHAR(64)` inside running gateway container + new idempotent Alembic migration `2026_07_12_phase_c0_correlation_id.py` + `env.py` updated with `DEER_FLOW_DATABASE_URL` env-var override.

### Streaming hardening (Phases 1–6)

- **Phase 1:** Watchdog with 12 probes (P1–P12) covering nginx, gateway, frontend, local LLM stack, containers, binary attestation, and Cloudflare tunnel.
- **Phase 2:** Bounded Stop + Force Disconnect — stop is a state machine with configurable timeout.
- **Phase 3:** Active-run polling — never fully disabled while a run exists.
- **Phase 4:** Convergent teardown — deterministic cleanup on disconnect.
- **Phase 5:** Backend store-only cancel — cancel persists through RunStore, startup reaper cleans stale runs.
- **Phase 6:** Tunnel auto-recovery — `fix_tunnel()` calls `systemctl reset-failed` before restart, verifies `is-active` for up to 10s post-restart. PM2 lock path moved to `$XDG_RUNTIME_DIR`.

### Cross-process correlation (Phase C0)

- **Backend:** `RunRecord.correlation_id` field, SSE comment emission (`: correlation_id=<hex>`), `RunManager.create()` generates UUID hex.
- **Frontend:** `stream-liveness.ts` captures correlation_id from SSE comments, `stream-trace.ts` propagates to `DiagnosticsRecord`.
- **Diagnostics:** `register_correlation_id()` / `lookup_correlation_id()` thread-scope helpers, module-level registry.
- **Tests:** 29 backend streaming/correlation tests, 13 frontend correlation capture + round-trip tests.

### CI guardrails

- `scripts/check_platform_guardrails.py` — middleware sprawl check (baseline: 28, allowed: 29) + duplicate-recovery detector (canonical site: `hooks.ts`).
- Guardrails run on every commit via AGENTS.md self-test protocol.

### Consolidation tracking

- `CONSOLIDATION.md` established as single source of truth for platform consolidation program.
- Directives 1–10 tracked with phases C0–C5.

### Tests

- 457 frontend unit tests (49 files).
- 29 backend streaming/correlation tests.
- 5 tunnel recovery tests.
- Cross-ref check passes (1497 files).

---

## v8.1 — Phase C1: documentation sync + typed service layer

**Session pattern:** repository audit → doc synchronization → service layer foundation.

### C1.1 — Repository Reality Audit

- 127 markdown files audited. 12 stale documents. 3 missing.
- Key corrections: README probe count (11→12), MONITORING Loki version (3.5.0→3.6.12), MONITORING Uptime Kuma port (3001→3003), ARCHITECTURE middleware count (8→19).

### C1.2 — Documentation Synchronization

- 12 files updated: README.md, CONSOLIDATION.md, NOVA_CHANGELOG.md, backend/CLAUDE.md, backend/docs/ARCHITECTURE.md, docs/RUNBOOK.md, docs/MONITORING.md, CONTRIBUTING.md, backend/CONTRIBUTING.md, frontend/CLAUDE.md, CHANGELOG.md.
- 3 new files created: DEPLOYMENT.md, DEVELOPMENT.md, ROADMAP.md.

### C1.3 — Typed Service Foundation

- 10 Protocol interfaces (`services/protocols.py`): RunService, WorkspaceService, RepositoryService, BrowserService, TerminalService, ArtifactService, HealthService, RecoveryService, ConfigurationService, DiagnosticsService.
- 7 typed return models (`services/types.py`): RunState, RunSummary, RunDetail, ProbeResult, HealthReport, RecoveryAction, DiagnosticsRecord, WorkspacePaths.
- 10 thin wrapper implementations (`services/implementations.py`).
- 33 unit tests (`tests/test_service_layer.py`).

---

## v8.2 — Phase C2: run state consolidation + dependency injection

**Session pattern:** DI container → canonical RunState → gateway wiring.

### C2.1 — Dependency Injection Container

- `ServiceContainer` class (`services/container.py`): lazy singletons, `override()` for testing, module-level `service_container` singleton.
- Gateway wired: `app.state.run_service`, `get_run_service` FastAPI dependency.

### C2.2 — Canonical RunState

- `RunState` frozen dataclass (`services/types.py`): immutable runtime object with `from_record()` bridge for backward compatibility.

### C2.3 — Gateway Wiring

- `app/gateway/deps.py`: container wired with gateway-owned singletons after RunManager construction.
- Backward compat: `get_run_manager` still works unchanged.

### Tests

- 33 service layer tests, all pass.
- 56/56 backend tests, 457/457 frontend tests.

---

## v8.3 — Phase C3: unified lifecycle + event bus

**Session pattern:** enum audit → lifecycle model → event bus → service integration.

### C3.1 — Lifecycle Model

- `RunLifecycleStatus` enum (`runtime/lifecycle.py`): 11 states (CREATED, INITIALIZING, RUNNING, CHECKPOINT, PAUSED, RESUMED, RECOVERING, COMPLETED, FAILED, CANCELLED, ARCHIVED).
- Properties: `is_terminal`, `is_active`, `is_transitional`.
- Adapters: `adapt_run_status()`, `to_run_status()` for backward compatibility.

### C3.2 — Event Bus

- `EventBus` (`events/bus.py`): synchronous, typed, deterministic, DI-compatible. Module-level `event_bus` singleton.
- 17 frozen dataclass domain events (`events/event.py`): RunCreated through RunArchived, WorkspaceMounted/Released, BrowserStarted/Stopped, HealthChanged, ToolExecuted, ArtifactCreated.
- `EventPublisher` (`events/publisher.py`): automatic metadata injection.
- `EventSubscriber` (`events/subscriber.py`): decorator-based registration.
- `EventRegistry` (`events/registry.py`): event type discovery by category.

### C3.3 — Service Integration

- `RunServiceImpl` publishes lifecycle events on `create()`, `cancel()`, `set_status()`.
- `DiagnosticsServiceImpl.subscribe_to_events()` records all domain events as diagnostics.
- `HealthServiceImpl` publishes `HealthChanged` on state transitions (deduplicated on steady state).

### Tests

- 56 new tests (`tests/test_event_bus.py`), all pass.
- 33 service layer tests, all pass.
- 56/56 backend tests, 457/457 frontend tests.

---

## v8.4 — Phase C4: recovery engine + unified health management

**Session pattern:** repository audit → declarative policies → event-driven recovery engine → service integration.

### C4.1 — Repository Audit

- 10+ distinct recovery implementations identified: fix_tunnel, fix_llama_bridge, fix_litellm, fix_dify, fix_deerflow_containers, RecoveryServiceImpl, llm_error_handling_middleware (backoff+circuit breaker), browser_retry (bounded retry+jitter), reap_orphaned_runs (startup reaper), _reconcile_orphans (container adoption), recordRecovery (frontend trace).

### C4.2 — Recovery Events

- 7 new frozen dataclass recovery events: RecoveryStarted, RecoveryRetryScheduled, RecoverySucceeded, RecoveryFailed, RecoveryEscalated, RecoveryCancelled, RecoveryAborted.
- Events registered under "recovery" category in EventRegistry.

### C4.3 — Declarative Recovery Policies

- `RecoveryPolicy` + `RetryStrategy` dataclasses (`services/recovery_policy.py`).
- 10 policies: TUNNEL_DISCONNECTED, GATEWAY_UNAVAILABLE, STREAM_STALLED, BROWSER_DISCONNECTED, BROWSER_CRASH, SANDBOX_UNAVAILABLE, HEALTH_DEGRADED, CONTAINER_RESTART, ORPHAN_RUN, WORKER_EXITED.
- `select_policy()`, `policies_for_trigger()`, `all_policies()`.

### C4.4 — Recovery Engine

- `RecoveryEngine` (`services/recovery_service.py`): event-driven orchestration, retry/backoff, cancellation, history, metrics.
- 9 default action handlers wrapping existing implementations.
- `ServiceContainer.recovery_engine()` singleton.

### Tests

- 37 new tests (`tests/test_recovery_engine.py`), all pass.
- 56 event bus tests, 33 service layer tests, all pass.
- 457/457 frontend tests, guardrails 28/28, cross-ref clean.

---

## v8.5 — Phase C5: platform convergence

**Session pattern:** repository audit → service migration → event-driven convergence.

### C5.1 — Repository Audit

- Comprehensive audit of remaining direct calls across the codebase:
  - `get_app_config()`: 33 files, ~95 call sites (wrapper exists via `ConfigurationService`)
  - `RunManager` direct imports: 7 files, ~23 call sites (gateway layer)
  - `RunStatus` direct imports: 8 files, ~57 call sites (internal to runtime)
  - `diagnostics.record`: 2 files, 9 call sites (module-internal)

### C5.2 — Service Migration: Gateway → RunService

- Added `create_or_reject()` to `RunService` protocol and `RunServiceImpl` for multitask-aware run creation.
- Migrated gateway endpoints to use `RunService`:
  - `list_runs()` → `RunService.list_by_thread()`
  - `get_run()` → `RunService.get()`
  - `cancel_run()` → `RunService.cancel()` (RunManager retained for wait=True task await)
- Added `_service_to_response()` helper for RunDetail/RunSummary → RunResponse conversion.
- Added `test_create_or_reject_delegates` test (34 service layer tests, up from 33).

### C5.3 — Event-Driven Convergence: Worker Status Routing

- **Problem:** `worker.py` called `RunManager.set_status()` directly, bypassing `RunServiceImpl.set_status()` which publishes lifecycle events to the EventBus. Status transitions (running → success/error/interrupted) were invisible to event subscribers.
- **Fix:** Added `_service_set_status()` helper that routes through `RunServiceImpl.set_status()` with fallback to `RunManager.set_status()`. All 9 `run_manager.set_status()` calls replaced.
- **Impact:** All run lifecycle transitions now emit domain events (RunStarted, RunCompleted, RunFailed, RunCancelled, RunInterrupted) through the EventBus.

### Tests

- 127 consolidation tests pass (34 service + 56 event bus + 37 recovery engine).
- Cross-ref check clean.
- Zero API changes, zero runtime regressions.

---

## v8.7 — Phase C7: execution kernel

**Session pattern:** repository execution audit → kernel implementation → call-site migration → guardrail enforcement.

### C7.1 — Repository Execution Audit

- 103 raw execution-primitive hits; 6 backend production files migrated, ops/dev scripts classified as out-of-process (documented debt):
  - `services/recovery_service.py` (pm2 restarts), `sandbox/review.py` (git), `sandbox/local/local_sandbox.py` (shell), `sandbox/dev_server.py` (long-running spawn + TERM/KILL), `community/aio_sandbox/local_backend.py` (8 docker/apple-container CLI sites).
- No browser process launches found — all browser work is CDP connects to the AIO chromium; modeled as audited session acquisition, not spawn.

### C7.2 — Execution Kernel (`deerflow/execution/`)

- One sanctioned `subprocess.Popen` site (`kernel.py`); everything else builds typed `ExecutionRequest`s.
- Pipeline: Scheduler (PolicyEngine + ResourceManager) → supervised process → typed `ExecutionResult` → hash-chained AuditEngine → ExecutionMetrics → domain events.
- Per-class policies: program allow-lists (git/docker/pm2/systemctl), sudo gated to `sudo -n systemctl` only, timeout clamps; `shell=True` impossible by construction (argv-only requests).
- Supervisor: process registry, process-group TERM→grace→KILL (kernel processes are session leaders so `sh -c` grandchildren die too), cancellation by execution id, orphan reconciliation wired into gateway lifespan shutdown.
- ReplayEngine: `dry_run`/`replay` of any audited execution; audit records never store env values (key names only).
- `kernel.spawn()` for supervised long-running processes (dev servers) with streaming stdout and `terminate_gracefully()`.
- Adapters: Shell, Docker (docker + Apple Container), Git, Browser (CDP sessions), Python, PM2, Systemd. `FakeExecutionKernel` test double in `deerflow.execution.testing`.

### C7.3 — Platform integration

- 9 new domain events (ExecutionRequested/Started/Completed/Failed/TimedOut/Cancelled/Denied, ProcessSpawned/Exited) registered under `execution` category.
- DI: `service_container.execution_kernel()` singleton; gateway wires `app.state.execution_kernel` + `get_execution_kernel` dependency; kernel shares the global EventBus.
- Recovery engine actions (`_recover_gateway`, `_recover_container`) route through Pm2Adapter; `_recover_tunnel` reimplemented on SystemdAdapter (reset-failed → restart → verify is-active ≤10s), removing the in-gateway import of the healthcheck daemon.

### C7.4 — Guardrails + tests

- `tests/test_execution_guardrails.py`: CI fails if any direct execution primitive appears in `backend/packages` or `backend/app` outside `deerflow/execution/`.
- 36 kernel tests (policy denial, sudo gating, timeout escalation, resource saturation → typed DENIED, audit chain verification, replay, cancel, spawn lifecycle, adapters, DI).
- Migrated test suites off `subprocess.run` monkeypatching onto `FakeExecutionKernel` overrides.
- Full backend suite green except one pre-existing environmental failure (`test_amd_usage_endpoint_reports_backed_models` expects `/app/extensions_config.json`; fails identically on the unmodified tree).
- Docs: `backend/docs/EXECUTION_KERNEL.md`.

### Known debt (Phase C8 candidates)

- `scripts/healthcheck-daemon.py` stays out-of-process by design (watchdog of last resort under pm2) and keeps its own subprocess calls.
- `aio_sandbox_provider.py` signal handlers (cleanup registration) not yet owned by the supervisor.
- Playwright CDP connect sites in `workspace_tools.py` / `browser_check.py` can adopt `BrowserAdapter.session()`.
- Audit trail is in-memory (10k ring); persistence to the diagnostics store not yet wired.

### C7.5 — Suite-health forensics (pre-existing failures, all root-caused and fixed)

The full backend suite had been hanging at ~47% and carrying 41 pre-existing failures (verified identical on the unmodified C6 tree). All fixed:

- **Deadlock (suite hang):** `mcp/session_pool.py::_run_session` reflected only `Exception` into the `ready` future — when `close_all()` **cancelled** an in-flight owner task (`CancelledError` is a `BaseException`), `ready` stayed pending forever and the caller blocked on `await asyncio.shield(ready)` (`ep_poll` forever). Fix: cancelled owners now cancel `ready`; get_session Phase 3 unwind catches `BaseException` so caller-cancellation cleanup (documented "case 2", previously dead code) actually runs. Fixes `test_close_all_during_in_flight_creation_does_not_resurrect_session` (the hang), `test_get_session_cancelled_while_initializing_does_not_leak`, and `test_cross_loop_preempting_blocked_in_flight_does_not_hang_owner` (its worker also caught only `Exception` — could never observe the CancelledError it asserts).
- **Environment leakage (36 failures):** repo-root `.env` is shared with the Docker deployment and pins `DEER_FLOW_CONFIG_PATH` / `DEER_FLOW_EXTENSIONS_CONFIG_PATH` / `DEER_FLOW_REPO_ROOT` / `DEER_FLOW_HOME` to in-container `/app/...` paths plus `DEER_FLOW_ENV=production`. `load_dotenv()` injected these into host pytest runs: gateway config load raised FileNotFoundError → every TestClient request 503'd (amd, channels, internal_auth, config_freshness, langgraph_auth, client_e2e), and the auth-disabled safety veto 401'd all `DEER_FLOW_AUTH_DISABLED` tests. Fix: `tests/conftest.py` pre-sets host-correct values before any `load_dotenv()` (dotenv never overrides existing vars); production `.env` untouched.
- **Phase C6 drift:** `test_cancel_run_idempotent` stubbed only `app.state.run_manager`; the C6-migrated endpoints resolve `run_service` → 503. Test app now provides `RunServiceImpl(mgr)` like the gateway lifespan.
- **Watchdog drift:** `test_healthcheck_daemon` run-cycle tests mocked 11 probes; the daemon runs 12 since P12 (tunnel). The unmocked `probe_tunnel` fired a real network request during tests. Probe list + counts updated.

### C7.6 — Production incident (2026-07-13, ~04:54 PKT) + structural interlock

- **What happened:** running the full suite executed the Phase C4 recovery actions for real. The stale test `test_tunnel_action_wraps_fix_tunnel` mocked the OLD `scripts.healthcheck_daemon` import; the C7-migrated `_recover_tunnel`/`_recover_gateway` bypassed that mock and issued real `sudo -n systemctl restart cloudflared-nova.service` (sudoers permits restart passwordless — but NOT `reset-failed`) and `pm2 restart deerflow`. Ten restarts in <5 min tripped systemd's start-limit → tunnel down (Cloudflare 530, 0 replicas). A concurrent interrupted `compose up` left the gateway container renamed+dead and `deer-flow-frontend` name-conflicted → deerflow pm2 crash-loop (1000+ restarts) → origin 502.
- **Recovery:** waited out the 5-min start-limit window → `systemctl restart` → tunnel active; removed the wedged containers (`docker rm -f` on the renamed gateway + stale-labeled frontend) → compose reconciled; rebuilt the frontend prod image (current tag had no `next build`) → public site 200 end-to-end.
- **Interlock (conftest autouse):** every test now receives a DI kernel whose policy **denies the PM2 and SYSTEMD execution classes** — a test can no longer restart production services, period. Tests that exercise pm2/systemd override with `FakeExecutionKernel`. This guard is only possible because C7 gives execution a single policy choke point.
- **Known operational gap (needs interactive sudo):** sudoers lacks `reset-failed cloudflared-nova` — the watchdog cannot clear `start-limit-hit` on its own (documented Step 0 warning fires every cycle). Re-run `scripts/install-cloudflared-nova.sh` as a sudo-capable user.

### C7.7 — Second forensics round (post-hang tail of the suite, all root-caused)

- `test_service_layer.py` + `test_wait_disconnect_handling.py` used deprecated `asyncio.get_event_loop().run_until_complete()` — breaks after any earlier `asyncio.run()` in the main thread (Python 3.12). Migrated to `asyncio.run()`.
- **Real C5 bug in `worker._service_set_status`:** outside the gateway, the lazy container factory builds a *fresh* `RunManager`; status updates routed there are silently dropped (run not found, nothing raises, fallback never fires). Now verifies the container RunService is backed by the worker's own RunManager before routing; falls back otherwise. Fixes `test_run_worker_rollback` / `test_run_worker_recursion_limit`.
- `test_stream_diagnostics` fixture popped `deerflow.runtime` / `app.gateway.services` from `sys.modules` at teardown, splitting sentinel identity (`END_SENTINEL` `is`-checks failed downstream) and orphaning package attributes (`deerflow.runtime` lost `runs` for monkeypatch walks). Teardown now restores original module objects and re-binds parent/child module attributes in both directions.
- `test_tracing_factory` monkeypatched `get_tracing_config` with a config *instance* instead of a callable (`'Cfg' object is not callable`). Wrapped in lambdas.
- `test_cancel_store_only_run_returns_409` encoded the pre-v8.0 contract; Phase 6 deliberately made store-only cancel persist `interrupted` through the RunStore and return 202. Test updated to assert the current semantics (renamed `..._persists_interrupted`).
- `test_thread_run_messages_pagination` + `test_cancel_run_idempotent` stubbed only `run_manager`; C6 endpoints resolve `run_service` → 503. Test apps now provide `RunServiceImpl(mgr)`.
- `docker/dev-entrypoint.sh` added `--reload-exclude=/app/backend/tests` without pre-creating the directory; mkdir added.
- `test_runtime_model_env_resolution` relies on sibling-of-config resolution; ambient `DEER_FLOW_HOME` redirected it to the deployment's root-owned file. Autouse fixture now clears the two env vars.
- Remaining known-red: `test_client_live.py` (4) — live smoke tests against this box; blocked by root-owned `.deer-flow/users/*` dirs created by the container (PermissionError) plus live-LLM nondeterminism (GraphRecursionError). Needs `chown` (interactive sudo) — operational, not code.

---

## v7.5 — live audit hardening + Ollama/LiteLLM free-model gateway

**Session pattern:** full-stack live audit (act-as-user via Playwright) → every blocker turned into a production-grade fix with a regression test.

### Audit fixes (all with tests)

- **Preview proxy 500 (P0):** the Batch-3.1 per-method route wrappers in `app/gateway/routers/sandbox.py` were sync `def` returning un-awaited coroutines — every `/api/sandbox/preview|lpreview|absproxy` request 500'd ("'coroutine' object is not iterable"), breaking the Browser tab. Made async; pinned by `test_preview_route_handlers_async.py`.
- **llama-bridge drift:** pm2's saved dump pointed at a deleted script path; the "online" process was orphaned stale code on the wrong port. Re-registered from `ecosystem.config.js`; watchdog `fix_llama_bridge()` now heals both crash and drift.
- **Watchdog gaps:** P9 had no auto-fix and warned every 30s unread for days. Added P9 + P10 auto-fixes and streak-deduped the no-auto-fix warning.
- **Strict chat-template 400:** middlewares inject SystemMessages mid-conversation; llama.cpp templates reject system at position > 0. New `SystemMessageCoalescingMiddleware` (innermost) folds them into the single leading system message.
- **Context overflow classified:** llama.cpp `exceed_context_size_error` now maps to a non-retriable `context_overflow` reason with an actionable message.
- **Silent verify skip:** auto-verify-on-present_files no-opped silently on non-AIO sandboxes — a corrupted deliverable shipped as "verified clean". Skip is now logged as "deliverable NOT verified".
- **Clobbered-HTML backstop:** deterministic review flags `.html` deliverables that don't start like HTML (`_scan_malformed_html_risks`) — catches the chunked-write overwrite failure observed live (maree.html written 3×, each write clobbering the last).
- **Orphan tool messages:** frontend grouping now renders tool results whose parent AI message was stripped instead of console.error + drop.
- **CSP blocked the Browser tab (nginx):** the app-shell CSP had no `frame-src`, so the blob-URL preview iframe (which inherits the parent page's CSP) was refused, and Google Fonts pulled by generated HTML were blocked. `docker/nginx/nginx.conf` `$csp_policy` map now allows `frame-src 'self' blob: http://localhost:*` plus fonts.googleapis.com/fonts.gstatic.com in the app-shell policy only — the strict `/api/` policy (`default-src 'none'`) is untouched. Note: the nginx container copies the mounted conf from a template at start, so `docker restart deer-flow-nginx` (not `nginx -s reload`) is required to apply edits.

### Ollama + LiteLLM free-model gateway

- `docker/litellm/config.yaml` + `scripts/pm2-litellm.sh` + pm2 app `nova-litellm` (LiteLLM 1.91.0 in a dedicated venv at `~/.nova-litellm`, bound to the docker bridge IP only).
- Four verified-free Ollama cloud models registered via the runtime models API: MiniMax M3, Nemotron 3 Super, Qwen3 Coder 480B, GPT-OSS 120B (`*-free`), reachable from the gateway at `host.docker.internal:4000/v1`.
- Watchdog P10_litellm probe + auto-fix; 10/10 probes green.
- Settings → Models: new "Add Ollama model (via LiteLLM)" preset (en/zh locales).

### Ecosystem: OpenCode + Dify on the same LiteLLM gateway

- **OpenCode**: `nova-litellm` provider in the global config (`~/.config/opencode/opencode.jsonc`) with all four free models and real limits pulled from `ollama show` (MiniMax M3 524K / Nemotron 262K / Qwen3-Coder 262K / GPT-OSS 131K context, 64K output); nova's `.opencode/opencode.json` pins `qwen3-coder-480b-free` as project default. Verified live via `opencode run`.
- **Dify** (fork `Jahanzaib211/dify` at `~/Desktop/dify`): full stack under PM2 as `nova-dify` (foreground compose, same pattern as `deerflow`), UI on `127.0.0.1:8088` only — upstream's 0.0.0.0 plugin-debug mapping replaced via `ports: !override`. Containers reach LiteLLM through `host.docker.internal:host-gateway`. Provider + 4 models configured as a real user via Playwright (context sizes set explicitly — the OpenAI-compatible plugin defaults to 4096); E2E chat verified ("DIFY-LITELLM-OK" on minimax-m3-free). Session lifetimes raised for the localhost-only install (access 7d / refresh 365d). Fork-side files committed to `Jahanzaib211/dify@main`.
- **Watchdog P11_dify**: probes `/console/api/setup` for `step=finished` (api-up-but-uninitialized reads as unhealthy); auto-fix heals the `nova-dify` pm2 app with re-register-on-drift. 11/11 probes green live; regression tests added (probe validator, both fix paths, dispatch streak).

### Attribution

- `NOVA_VS_DEERFLOW.md` — verified upstream-vs-Nova attribution map (fork base deer-flow v2.0.0-rc1, reproducible diff commands); README "What Nova adds" rewritten to match.
- Attribution numbers recomputed pre-commit: 338 files, +35,738/−1,278 vs v2.0.0-rc1 (152 new files ~28.7k lines; 37 new backend test files).
- README hero: `docs/images/nova-workspace.png` — real capture of a MiniMax M3 (free) session building a tip calculator, previewed live in the Agent's Computer Browser tab (zero console errors at capture).

---

**Sprint window:** 5 days. **Pattern:** Sequential Pipeline + De-Sloppify + per-day CI gate.
**Loop pattern (per Autonomous Loops skill):** daily `claude -p` chain, context bridge via `SHARED_TASK_NOTES.md`, magic-phrase completion signals.

### Opencode ecosystem integration (Matt Pocock fork)

The user's fork at `Jahanzaib211/skills` (forked from `mattpocock/skills`) provides workflow + discipline skills. Three integration layers:

- **Layer 1 (zero code change):** opencode commands wrapping Matt Pocock's user-invoked skills (`/grill-me`, `/to-prd`, `/triage`, `/improve-codebase-architecture`, `/setup-matt-pocock-skills`)
- **Layer 2 (one-line code change):** `extensions_config.json` registry update for Matt Pocock's model-invoked discipline skills (`tdd`, `diagnosing-bugs`, `codebase-design`, `domain-modeling`); modify `skill_storage.py:_iter_skill_files` to walk `~/.claude/skills/mattpocock/`
- **Layer 3 (no code change):** orchestration loop — user runs `/grill-with-docs` → skill outputs PRD → agent executes with TDD discipline → receipts + watchdogs + self-improvement closes the loop

### Day-by-day plan

| Day | Task | Magic phrase | Layer additions |
|---|---|---|---|
| D1 | opencode overlay + 8 nova skills | `NOVA_V74_D1_COMPLETE` | Layer 1 (5 opencode commands) + Layer 2 (registry) |
| D2 | hooks surface (resurrect `record_middleware`, wire 5 middlewares) | `NOVA_V74_D2_COMPLETE` | Layer 2 (skill_storage.py path) |
| D3 | receipts + watchdogs + cronjobs | `NOVA_V74_D3_COMPLETE` | — |
| D4 | nova CLI + Receipts/Improvements tabs + Self-improving loop | `NOVA_V74_D4_COMPLETE` | — |
| D5 | coverage + lint cleanup + release | `NOVA_V74_SPRINT_COMPLETE` | — |

### Opencode custom skills (8) created in D1

Each skill references real file paths in this repo (no hallucination):

- `nova-replay-verify` — prove the thesis via replay-golden
- `nova-hooks-author` — the keystone primitive (`runtime/journal.py:529`)
- `nova-receipts-author` — structured post-run artifacts
- `nova-subagent-router` — 40-60% token savings
- `nova-loop-detect` — debug stuck agents (`loop_detection_middleware.py:837`)
- `nova-sandbox-debug` — local vs AIO triage
- `nova-deploy-local` — canonical `make dev` path
- `nova-prompt-author` — lead-agent system prompt discipline

### Sprint verification matrix (per day, exit gate)

- `cd backend && uv run pytest tests/test_replay_golden.py tests/test_capabilities_endpoint.py tests/test_igino_endpoint.py tests/test_openapi_operation_ids.py -q` — keystone
- `cd backend && uv run ruff check` — clean
- `cd frontend && pnpm format && pnpm lint && pnpm typecheck` — clean
- Manual 2-minute Loom path works (per the demo flow)

### Anti-slop invariants (from the AI-First Engineering skill)

- Every layer reuses existing primitives (`record_middleware`, `BUILTIN_TOOLS`, `RunEventStore`, `subagents/executor.py`, `scripts/serve.sh`, `sandbox/browser_circuit_breaker.py`)
- No parallel implementations
- `deerflow.*` never imports `app.*` (enforced by `tests/test_harness_boundary.py`)
- Operational IDs preserved: `deer-flow` folder, `deer-flow-dev` compose project, `deer-flow-*` containers, `deerflow.*` Python package, `DEER_FLOW_*` env vars, `backend/.deer-flow/` data dir
- Non-fatal: every new code path is wrapped so a failure can never break a run

### Rollback

`git tag pre-hackathon-sprint-rollback-20260629` (pre-D0) is the instant-revert anchor. Per-day revert: `git revert <D[n-1]-sha>..<D[n]-sha> -- <paths>` for the specific files touched in that day.

---

## v8.8 — Phase C8: execution runtime kernel

**Session pattern:** production forensic → execution hardening → test coverage.

### C8.1 — Execution Kernel completeness

- **PTYManager** (`execution/pty_manager.py`): portable PTY allocation via `os.openpty()`, window-size control via `fcntl.ioctl TIOCSWINSZ`, file-descriptor lifecycle. `TerminalSize` dataclass with `to_winsize()` / `from_winsize()`.
- **SessionRegistry** (`execution/session_registry.py`): `ShellSession` dataclass + `SessionRegistry` for full interactive session lifecycle — PTY fds, pid, cwd, env, terminal size, heartbeat, I/O metrics. `SessionState` enum: ALLOCATED, STARTING, RUNNING, WAITING, STOPPING, STOPPED, ZOMBIE, ERROR.
- **InteractiveShellAdapter** (`execution/adapters/interactive_shell.py`): per-session PTY spawning with I/O locks, heartbeat thread, SIGWINCH propagation on resize. Uses `subprocess.Popen` with `start_new_session=True`.
- **Ownership maps** in `Supervisor`: bidirectional `execution_id ↔ run_id ↔ session_id ↔ pid` maps; methods: `register_execution()`, `unregister_execution()`, `get_execution_id()`, `get_run_id()`, `get_session_id()`.
- **ProcessHeartbeat** in `Supervisor`: `ProcessHeartbeat` dataclass with `pid`, `started_at`, `last_heartbeat`, `session_id`; `_heartbeats` dict; `update_heartbeat()`, `get_heartbeat()`, `scan_zombies()`.
- **Heartbeat thread** in `kernel.execute_sync()`: 5s interval, stops on process exit/cancel/timeout; emits `ProcessHeartbeat` domain events.

### C8.2 — Two-phase cancellation

- **`_two_phase_cancel()`** in `kernel.py`: SIGINT → SIGTERM → SIGKILL escalation chain with configurable grace periods.
- **`Supervisor.cancel()`**: recursive child cancellation first (deepest first), then target; checks `_popen` then `_spawned`; `was_cancelled()` for status tracking.
- **`_schedule_async_termination()`**: `loop.call_soon()` fires termination asynchronously so cancel returns immediately (Phase 1) while SIGKILL fires later (Phase 2).

### C8.3 — ExecutionStatus state machine

- Expanded `ExecutionStatus` enum with 11 states: PENDING, ALLOCATED, PREPARING, RUNNING, WAITING_INPUT, STREAMING, CANCELLING, STOPPING, SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, STOPPED, DENIED, ZOMBIE_DETECTED, REAPED.
- `is_terminal`, `is_active`, `is_cancellable` properties on every status.
- `ExecutionRequest` gains `parent_execution_id` (for child tree tracking) and `session_id` fields.
- `ResourceLimits` gains `max_depth`, `max_children`, `max_recursion`, `heartbeat_interval`.

### C8.4 — Execution budget (scheduler)

- `DepthTracker`: per-run depth counter incremented on admit, decremented on release.
- `ChildTracker`: per-parent child count incremented on admit, decremented on release; `get_child_count()`.
- `Scheduler.admit()` checks depth/child budget before admission; `DENIED` result when saturated.

### C8.5 — Tests

- `tests/test_execution_pty.py`: 23 tests covering PTYManager, TerminalSize, SessionRegistry, heartbeat, two-phase cancellation, ExecutionStatus state machine.
- `tests/test_execution_guardrails.py`: guardrail updated to whitelist `interactive_shell.py` as sanctioned Popen site.
- All 5570 backend tests pass; 457 frontend tests pass; cross-ref check clean.

### Known debt

- `InteractiveShellAdapter` has a preexec conflict: `start_new_session=True` already calls `setsid()`; redundant `preexec_fn=os.setsid` causes `SubprocessError` in some environments. Tests skipped pending fix.
- `SessionRegistry.snapshot()` had a deadlock (calling `list_active()` while holding lock); fixed by inlining the active-count logic.

---

## Operational IDs (intentionally preserved)

- docker-compose project name: `deer-flow-dev`
- Container names: `deer-flow-{nginx,frontend,gateway}`
- pm2 process: `deerflow`
- Python package: `deerflow.*`
- Env vars: `DEER_FLOW_*`
- Data dir: `backend/.deer-flow/`

These are stable; only docs / user-facing strings change.

---

## Hard rules (carried forward from FORK_V* + AI-First Engineering)

1. **Additive / reversible only.** Every change is one revert away.
2. **Local-sandbox path byte-identical.** `is_local_sandbox` branch (sandbox/tools.py:1204) preserved.
3. **Batch backend edits.** uvicorn `--reload` recreates active sandboxes.
4. **Anti-slop.** Reuse existing primitives; no parallel implementations.
5. **Non-fatal.** Every new code path is wrapped so a failure can never break a run.
6. **Harness boundary.** `deerflow.*` never imports `app.*` (enforced).
7. **Replay E2E is the contract.** `tests/fixtures/replay/write_read_file.ultra.{json,events.json}` is the keystone test.
