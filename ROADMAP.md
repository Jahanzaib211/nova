# Nova Roadmap

> Platform consolidation phases and future direction.

**Audience:** contributors, stakeholders.
**Last Updated:** Phase C4 (2026-07-12)
**Related:** [CONSOLIDATION.md](CONSOLIDATION.md), [NOVA_CHANGELOG.md](NOVA_CHANGELOG.md)

## Current Phase: C5 — Platform Convergence

**Status:** in progress

- Gateway services migrated to `RunService` protocol
- Worker status updates routed through service layer for EventBus emission
- `create_or_reject()` added to `RunService` protocol and implementation
- 127 consolidation tests pass

## Consolidation Phases

| Phase | Title | Status | Depends On |
|-------|-------|--------|------------|
| C0 | Foundation (correlation, CI guardrails) | **complete** | — |
| C1 | Documentation sync + typed service layer | **complete** | C0 |
| C2 | Run state consolidation + dependency injection | **complete** | C1 |
| C3 | Unified lifecycle + event bus | **complete** | C2 |
| C4 | Recovery engine + unified health management | **complete** | C2, C3 |
| C5 | Tool protocol standardization | pending | C2 |
| C6 | Workspace/Repository abstraction | pending | C2 |
| C7 | Deployment, HA, production hardening | pending | C3–C6 |
| C8 | Performance optimization and scaling | pending | C7 |
| C9 | Product features and extensibility | pending | C8 |

## Phase Details

### C0 — Foundation (complete)

- Cross-process correlation via `correlation_id`
- SSE comment emission + frontend capture
- Backend diagnostics propagation
- Streaming hardening (watchdog, bounded teardown, convergent cleanup)
- Tunnel auto-recovery
- CI guardrails (middleware sprawl, duplicate-recovery)
- Consolidation tracker (`CONSOLIDATION.md`)

### C1 — Documentation + Typed Service Layer (complete)

- Repository reality audit (127 files)
- Documentation synchronization (12 files updated, 3 created)
- Typed service protocols (10 Protocol interfaces)
- Typed return models (7 dataclasses)
- Thin wrapper implementations (10 services)
- 33 unit tests

### C2 — Run State Consolidation + Dependency Injection (complete)

- Repository audit of all direct imports
- `ServiceContainer` with lazy singletons + `override()` for testing
- `RunState` frozen dataclass with `from_record()` bridge
- Gateway wired: `app.state.run_service`, `get_run_service` dependency
- Backward compat: `get_run_manager` still works unchanged

### C3 — Unified Lifecycle + Event Bus (complete)

- Canonical `RunLifecycleStatus` enum (11 states with `is_terminal`, `is_active`, `is_transitional`)
- Adapters: `adapt_run_status()`, `to_run_status()` for backward compatibility
- 17 frozen dataclass domain events (lifecycle, workspace, browser, health, tool, artifact)
- `EventBus` — synchronous, typed, deterministic, DI-compatible
- `EventPublisher` — automatic metadata injection
- `EventSubscriber` — decorator-based registration
- `EventRegistry` — event type discovery by category
- `RunServiceImpl` publishes lifecycle events on create/cancel/set_status
- `DiagnosticsServiceImpl.subscribe_to_events()` records all events
- `HealthServiceImpl` publishes `HealthChanged` on state transitions
- 56 unit tests, all pass

### C4 — Recovery Engine + Unified Health Management (complete)

- `RecoveryEngine` — event-driven recovery orchestration via EventBus
- 10 declarative recovery policies with `RetryStrategy` (backoff, jitter, caps)
- 7 recovery events: RecoveryStarted, RecoveryRetryScheduled, RecoverySucceeded, RecoveryFailed, RecoveryEscalated, RecoveryCancelled, RecoveryAborted
- 9 default action handlers wrapping existing implementations (fix_tunnel, PM2 restart, etc.)
- Recovery history (bounded in-memory) and structured metrics (8 counters)
- Cancellation support (per-policy and bulk)
- `ServiceContainer.recovery_engine()` singleton wired with EventBus
- 37 unit tests, all pass

### C5 — Platform Convergence (in progress)

- Gateway `list_runs`, `get_run`, `cancel_run` migrated from `RunManager` to `RunService`
- `RunService.create_or_reject()` added for multitask-aware run creation
- Worker `_service_set_status()` routes all status transitions through service layer
- EventBus now receives all lifecycle events (RunStarted, RunCompleted, RunFailed, etc.)
- 127 consolidation tests, all pass

### C6 — Workspace/Repository Abstraction (pending)

- Remove shell-driven infrastructure where practical
- Workspace and repository abstractions

### C7 — Deployment, HA, Production Hardening (pending)

- High availability patterns
- Production deployment hardening

### C8 — Performance Optimization and Scaling (pending)

- Performance profiling and optimization
- Scaling strategies

### C9 — Product Features and Extensibility (pending)

- New product features built on consolidated architecture
- Extension points and plugin system

## Design Principles

1. **Single coherent operating system** — not a collection of AI features
2. **Reduce architectural complexity** — every sprint should simplify
3. **Increase operational reliability** — self-healing, observability, testing
4. **Strengthen foundation first** — before expanding capabilities
5. **Implementation is source of truth** — docs follow code, not vice versa

## Constraints

- No new user-facing features until consolidated
- No references to the local LLM gateway service name in code
- No references to the model name it wraps in code (allowed only as literal model identifiers in config)
- `deerflow` namespace is allowed (historical)
- TDD mandatory for all new features
- All commits must pass self-test protocol
