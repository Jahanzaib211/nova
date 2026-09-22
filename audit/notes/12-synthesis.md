# Phase 12 — Gates, blast-radius, roadmap

## (a) Missing CI gates (17 workflows today; local-ci.yml is the real gate)
CI HAS: backend lint/format/tests, blocking-IO gate, coverage (no floor), cross-ref, frontend lint/typecheck/test/build, pip-audit+pnpm audit (both `|| true`), CodeQL, Playwright e2e, replay-golden, lighthouse, container build.

CI is MISSING:
- Coverage **floor** + include `app/` (CI-002) — auth/guardrail/classifier coverage is unmeasured.
- Dependency audit that **fails** (CI-001) — currently `|| true`.
- **gitleaks / Trivy(fs+image) / Grype / SBOM** (GATE-SEC) — README claims them; only CodeQL exists.
- **Mutation testing** on security-critical modules (GATE-SEC) — classifier corpus is self-selected.
- **Migration check** (GATE-MIGRATE) — alembic head vs models.
- **Docs-claim test** (GATE-DOCS) — counts drift.
- Bundle-size budget, TTFT/perf smoke, license-compliance, no-network-in-unit-tests, no-skip/xfail-on-main, sandbox-escape regression corpus.

## (b) Blast-radius map (change-risk ranked) + baseline-first regression suite
1. **Sandbox classifier + privileged sandbox** (`sandbox_audit_middleware.py`, `config.yaml` privileged, `local_backend.py`) — security posture of the whole host (SEC-011/010). *Baseline needed first:* the bypass corpus (SEC-010) as tests + a test asserting default is non-privileged tools-layer.
2. **Streaming pipeline** (`runtime/stream_bridge`, `services.py`, `task_events.py`, FE `sandbox/hooks.ts`) — every feature. *Baseline:* SSE reconnect + thread-switch + load/soak tests; the memory→redis switch (PERF-001) needs a bridge-parity test.
3. **Graph/state** (`agents/lead_agent`, `thread_state.py`, 28 middlewares) — all agents. *Baseline:* middleware-order + fail-closed + state-schema tests (some exist).
4. **Auth/guardrail chain** (`auth/jwt.py`, `csrf_middleware.py`, `guardrails/`, `auth_middleware.py`) — access control. *Baseline:* good coverage exists; add coverage floor + aud/iss/exp tests (SEC-015).
5. **Persistence/migrations** (`persistence/engine.py`, migrations) — data integrity. *Baseline:* alembic up/down + create_all-vs-migration parity (PROD-001).
6. **IM adapters** (`app/channels/*`) — mostly isolated blast radius. *Baseline:* default-deny ACL test (SEC-013).

High import fan-in hubs to touch carefully: `runtime.user_context` (51), `config.app_config` (43), `config.paths` (42).

## (c) Roadmap — see AUDIT.md §7.
