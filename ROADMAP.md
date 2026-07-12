# Nova Roadmap

> Platform consolidation phases and future direction.

**Audience:** contributors, stakeholders.
**Last Updated:** Phase C1 (2026-07-12)
**Related:** [CONSOLIDATION.md](CONSOLIDATION.md), [NOVA_CHANGELOG.md](NOVA_CHANGELOG.md)

## Current Phase: C1 — Documentation + Typed Service Foundation

**Status:** in progress

- Synchronize all documentation with implementation
- Create typed service layer foundation (Protocol interfaces + thin wrappers)
- No runtime behavior changes

## Consolidation Phases

| Phase | Title | Status | Depends On |
|-------|-------|--------|------------|
| C0 | Foundation (correlation, CI guardrails) | **complete** | — |
| C1 | Documentation sync + typed service layer | in progress | C0 |
| C2 | Centralized run state + lifecycle | pending | C1 |
| C3 | Self-healing + unified observability | pending | C2 |
| C4 | Tool interface standardization | pending | C1, C2 |
| C5 | Replaceability + reliability | pending | C1–C4 |

## Phase Details

### C0 — Foundation (complete)

- Cross-process correlation via `correlation_id`
- SSE comment emission + frontend capture
- Backend diagnostics propagation
- Streaming hardening (watchdog, bounded teardown, convergent cleanup)
- Tunnel auto-recovery
- CI guardrails (middleware sprawl, duplicate-recovery)
- Consolidation tracker (`CONSOLIDATION.md`)

### C1 — Documentation + Typed Service Layer (in progress)

- Repository reality audit
- Documentation synchronization (12 files)
- New documentation (DEPLOYMENT.md, DEVELOPMENT.md, ROADMAP.md)
- Typed service protocols (Protocol/ABC interfaces)
- Thin wrapper implementations (no behavior changes)
- Service unit tests

### C2 — Centralized Run State + Lifecycle (pending)

- Single source of truth for run state
- Standardized lifecycle management
- Eliminate duplicate logic
- Centralize run state across frontend and backend

### C3 — Self-Healing + Unified Observability (pending)

- Strengthen self-healing capabilities
- Unified observability pipeline
- Enterprise reliability patterns

### C4 — Tool Interface Standardization (pending)

- Standardize tool interfaces
- Move infrastructure out of prompts
- Tool schema consistency

### C5 — Replaceability + Reliability (pending)

- Make every component replaceable
- Enterprise reliability guarantees
- Full backward compatibility

## Design Principles

1. **Single coherent operating system** — not a collection of AI features
2. **Reduce architectural complexity** — every sprint should simplify
3. **Increase operational reliability** — self-healing, observability, testing
4. **Strengthen foundation first** — before expanding capabilities
5. **Implementation is source of truth** — docs follow code, not vice versa

## Constraints

- No new user-facing features until consolidated
- No `ali-kernel` or `ornith` references in code
- `deerflow` namespace is allowed (historical)
- TDD mandatory for all new features
- All commits must pass self-test protocol
