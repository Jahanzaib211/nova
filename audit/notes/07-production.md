# Phase 7 — Production readiness

| Concern | State | Evidence |
|---|---|---|
| DB migrations | ⚠️ **create_all at boot, Alembic not run on deploy** → PROD-001 | engine.py:195,205; versions/ has 10+ migrations; no `alembic upgrade` in deploy path |
| Graceful shutdown | ✅ bounded lifespan shutdown hooks, SIGTERM handled | app.py:77,547; deps.py:65 |
| Horizontal scale | ⚠️ single-replica-correct: InMemoryRateLimiter default, in-memory task_events bridge, in-memory audit deque; code comments acknowledge replica-split bugs | app.py:410-418; rate_limiter.py; task_events.py:144; execution/audit.py |
| Observability | 🟡 correlation_id emitted as first SSE frame (services.py:554-574), request_ids on credit ops; provisioner metrics proxied (infra_client.py:55). No gateway-native /metrics Prometheus endpoint found (monitoring stack is separate `make monitoring-up`) | — |
| Checkpoint retention | ✅ codified after the 2026-08-19 outage | runtime/events/retention.py; matches memory note |
| Backups/DR | ❔ not verified here (Postgres in `deer-flow-postgres`); host-death recovery of threads/memory/audit not established | — |
| Cost controls | ❌ **no per-user LLM budget**; rate limit only on auth endpoints → PROD-002 | app.py:419; credit tables exist but not a hard runtime ceiling |
| Sandbox lifecycle | ✅ warm pool + idle eviction + orphan reconciliation | aio_sandbox_provider.py:839,1036; test_sandbox_orphan_reconciliation_e2e.py |
| Supply chain | ⚠️ lockfiles present (uv.lock, pnpm-lock); running sandbox image is `:latest` not digest (SEC-012) | — |
| Process mgr | PM2 (`ecosystem.config.js`) drives docker compose. Reasonable for single-host; not k8s despite `k8s/` dir (57 files) existing — confirm which is authoritative | ecosystem.config.js; k8s/ |

## Key gaps: PROD-001 (migrations not applied on deploy), PROD-002 (no cost budget). Both P1.
## Cross-refs: SEC-011 (privileged host risk), SEC-013 (open bot amplifies cost/abuse), CI-002 (coverage), SEC-014 (durable audit).
