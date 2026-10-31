# Phase 6 — Testing audit (quality, not quantity)

## Verdict: high-quality suite, thin on cross-cutting gates.
- **Substance:** sampled `test_auth_middleware.py` = 29 test fns / 39 asserts, real behavior. 485 `is not None` asserts across the whole suite (~7% of tests) — normal, not hollow-dominant.
- **Concurrency IS tested:** `test_audit_writer_concurrent.py` and 69 files touch concurrency/race — the audit prompt's "missing concurrency tests" concern does NOT apply here.
- **Skips (26) are all legitimate platform guards:** reasons are "POSIX shells", "PTY requires POSIX", "Docker not available", "not a git checkout", "symlink semantics differ on Windows". No hidden functional skips. 1 xfail (Phase 0).
- **Chaos/resilience partially covered:** `test_gateway_lifespan_shutdown.py` (bounded shutdown when a channel hangs), `test_tunnel_recovery.py`, `test_dev_server_watchdog.py`. Good.
- **Real-Docker/network tests:** ~44 files (e.g. `test_sandbox_orphan_reconciliation_e2e.py`, `test_nginx_preview_headers.py`). Marked via skip-if-unavailable, but they inflate the "unit" run to 7 min and run real containers — should be a separate marker/lane.
- **Flake risk:** 20 `time.sleep()` in tests (heartbeat/PTY timing). Acceptable but a future de-flake target.

## Missing categories (report absences)
1. **Coverage floor** — measured (`make coverage`) but no `fail_under`, and `app/` (gateway, auth, routers) is excluded from `--cov` (CI-002). Coverage on auth/guardrail/classifier is therefore unknown.
2. **Mutation testing** — `mutmut` not installed/configured. The security-critical classifier (SEC-010) has only its own curated corpus; no mutant-kill signal. → recommend `mutmut` on `sandbox_audit_middleware.py`, `auth/jwt.py`, `guardrails/`.
3. **Sandbox-escape regression suite** — the classifier corpus is curated to pass; there is no convention that each discovered bypass (SEC-010) becomes a regression test. Add the bypass list as failing-then-fixed tests.
4. **FE↔BE contract/OpenAPI sync** — TEST-001; FE types hand-written.
5. **Load/soak on the SSE path** — none found; the `events[]` accumulation (Phase 4) and subprocess-pipe teardown warning (Phase 3) are untested under sustained streaming.
6. **Migration up/down** — Alembic present; no test exercises up→down→up (Phase 7).

## Findings: TEST-001 (contract), plus CI-002 (coverage floor, Phase 0/12).
