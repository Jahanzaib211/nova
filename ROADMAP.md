# Nova Roadmap

> Platform consolidation phases and future direction.

**Audience:** contributors, stakeholders.
**Last Updated:** Phase C1 (2026-07-12)
**Related:** [CONSOLIDATION.md](CONSOLIDATION.md), [NOVA_CHANGELOG.md](NOVA_CHANGELOG.md)

## Current Phase: C2 — Run State Consolidation + Dependency Injection

**Status:** complete

- Repository audit of all direct imports
- Dependency injection container (`ServiceContainer`)
- Canonical `RunState` immutable dataclass
- Gateway wired with service interfaces
- `get_run_service` FastAPI dependency available

## Consolidation Phases

| Phase | Title | Status | Depends On |
|-------|-------|--------|------------|
| C0 | Foundation (correlation, CI guardrails) | **complete** | — |
| C1 | Documentation sync + typed service layer | **complete** | C0 |
| C2 | Run state consolidation + dependency injection | **complete** | C1 |
| C3 | Unified lifecycle state machine | pending | C2 |
| C4 | Self-healing + RecoveryService | pending | C2, C3 |
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

### C3 — Unified Lifecycle State Machine (pending)

- Canonical run lifecycle states
- State transition validation
- Event emission standardization

### C4 — Self-healing + RecoveryService (pending)

- Wire RecoveryService to actual recovery paths
- Automated recovery for tunnel, gateway, containers

### C5 — Tool Protocol Standardization (pending)

- Standardized tool interfaces
- Tool schema consistency

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
