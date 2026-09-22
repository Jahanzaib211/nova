# Phase 0 — Baseline & inventory (2026-09-16)

Branch `regression/2026-08-25-ws-sweep`, HEAD 2feb0fc9 (2026-09-09). 24 tracked files locally modified, 4 untracked (see `git status`). Live Nova runs on this host as `pm2 nova` → `scripts/pm2-deerflow.sh` → docker compose (`deer-flow-gateway`, `deer-flow-frontend`, `deer-flow-nginx`, `deer-flow-postgres` pgvector/pg16, browserless, searxng, crawl4ai, autoheal — all "Up 3 days (healthy)"). Everything below was run natively on the host, niced, single process; `act`-based `make ci/ci-fast` was **not** run (Docker load on a live box — CI-003).

## Snapshot

| Check | Result | Source |
|---|---|---|
| Toolchain | Python 3.12.13, Node 26.8.1, pnpm 10.26.2, uv 0.11.33, docker 29.6.2 | raw/00-inventory.txt, raw/00-doctor.txt |
| `make doctor` | **exit 1** — 2 errors: nginx binary missing on host (runs in container), `config.yaml loadable` fails because DATABASE_URL lives only in compose env → DOC-001 (false negative) | raw/00-doctor.txt |
| Backend pytest (`tests/`, excl. blocking_io) | **6900 passed, 21 skipped, 1 xfailed**, 153 warnings, 7m02s | raw/00-pytest.txt |
| Backend blocking-IO gate | 20 passed, 13s | raw/00-pytest-blocking-io.txt |
| Frontend typecheck | clean, 10s | raw/00-fe-typecheck.txt |
| Frontend lint | clean, 0 warnings, 20s | raw/00-fe-lint.txt |
| Frontend vitest | **689 passed / 76 files**, 9.6s | raw/00-fe-test.txt |
| Playwright | not run (30 spec files under frontend/tests/e2e*, plus `live/`) | — |
| pnpm audit | **2 critical, 37 high, 39 moderate, 9 low** (all reachable from prod deps per pnpm) — Next.js 16.2.12 < 16.3.3 → DEP-001 | raw/00-pnpm-audit.json |
| pip-audit (193 locked prod pkgs) | 1 vuln: langgraph-checkpoint-sqlite 3.0.3 PYSEC-2026-3636 → DEP-002 | raw/00-pip-audit.json |
| gitleaks (tracked + history) | 0 real secrets; 24 history hits are all test fixtures/doc examples → SEC-001; live `.env` duplicated into 2 stray `.kilo/worktrees/*/.env` → SEC-002 | raw/00-gitleaks-*.json |
| Coverage gate | measured in CI (`make coverage`, harness only) but **no fail_under, app/ excluded** → CI-002 | backend/Makefile:14 |

## Repo size (tracked files, LOC of code files)

| Area | files | LOC |
|---|---|---|
| backend/app (gateway, channels) | 98 | 27,312 |
| backend/packages/harness (deerflow.*) | 396 | 70,801 |
| backend/tests | 420 (401 `test_*.py`) | 124,642 |
| frontend/src | 453 | 57,507 |
| frontend/tests | 105 | 16,122 |
| skills | 102 | 6,676 |
| docker | 42 | 4,517 |
| scripts | 63 | 14,064 |
| k8s | 57 | 9,287 |
| .github/workflows | 17 | 1,495 |

Test:code ratio backend ≈ 1.27 LOC test per LOC code. Frontend ≈ 0.28.

## Stack fingerprint
- Backend: FastAPI ≥0.115, uvicorn, httpx ≥0.28, langgraph ≥1.1.9, langchain ≥1.2.15 (ceiling comment in harness pyproject re: vuln-free langchain unsatisfiable), langgraph-api, pydantic ≥2.12.5, sqlalchemy[asyncio] 2.x + alembic, pyjwt ≥2.13, cryptography ≥43, fakeredis (tests). Checkpointer + DB = Postgres (config.yaml:373-388, migrated 2026-08-21 after SQLite hit 59.4 GB).
- Frontend: Next 16.2.12, React 19, TypeScript, @tanstack/react-query 5.90, vitest, Playwright 1.59. No swr/zustand.
- CI (17 workflows): local-ci.yml is the real gate (lint/format/tests/blocking-io/coverage/cross-ref/frontend lint+typecheck+test+build/pip-audit+pnpm audit **both `|| true`** → CI-001). Separate: codeql, e2e-tests (Playwright), replay-e2e (golden replay + real-backend Playwright), lighthouse, container.yaml, docs-check, cross-ref.

## Churn hotspots (last 300 commits)
backend/CLAUDE.md (32), i18n locales (31/31/29), agent-computer-panel.tsx (25), docker-compose-dev.yaml (22), NOVA_CHANGELOG.md (20), core/sandbox/hooks.ts (18), config.example.yaml (18), routers/sandbox.py (18), core/threads/hooks.ts (17), files-tab.tsx (17), sandbox/tools.py (15), aio_sandbox/local_backend.py (13), healthcheck-daemon.py (12), aio_sandbox_provider.py (12), nginx.conf (11), sandbox/dev_server.py (11). → Sandbox provisioning (backend+frontend) and the agent-computer panel are the highest-churn, highest-regression-risk areas. Single author (git shortlog).

## Notes for later phases
- "Unit" suite runs real Docker: `tests/test_sandbox_orphan_reconciliation_e2e.py` (33s/22s/18s slowest) and `test_nginx_preview_headers` shells out to nginx — Phase 6.
- `PytestUnraisableExceptionWarning: Event loop is closed` from asyncio subprocess pipes in `test_workspace_e2e.py` — leak signal, Phase 3.
- 21 skipped / 1 xfail — enumerate in Phase 6.
