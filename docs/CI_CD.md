# CI/CD Pipeline

Nova's CI/CD runs on GitHub Actions with 17 workflows. You can also run the full CI locally using `nektos/act`.

## Local CI

```bash
make ci          # full local CI (all gates)
make ci-fast     # lint + tests only
make ci-gate     # gate summary only
```

Requires [nektos/act](https://github.com/nektos/act) installed. Configuration is in `.actrc`.

## Workflow Architecture

### Core Test Workflows

| Workflow | Trigger | What it validates |
|---|---|---|
| `backend-unit-tests.yml` | push/PR to main | 6,928 backend unit tests (`make test`) |
| `backend-blocking-io-tests.yml` | push/PR to main | Blockbuster runtime gate on async blocking IO |
| `frontend-unit-tests.yml` | push/PR to main | 681 frontend unit tests (`pnpm test`) |
| `frontend-build.yml` | push/PR to main | Next.js production build (`pnpm build`) |
| `e2e-tests.yml` | push/PR to main | Playwright E2E tests (Chromium, mocked backend) |
| `replay-e2e.yml` | push/PR to main | Replay golden E2E tests (deterministic regression) |

### Quality Gates

| Workflow | Trigger | What it validates |
|---|---|---|
| `lint-check.yml` | push/PR to main | Ruff lint + format check |
| `cross-ref.yml` | push/PR to main | No forbidden cross-project references |
| `docs-check.yml` | push/PR to main | Documentation link validation |
| `codeql.yml` | weekly + PR | CodeQL security analysis |

### Automation

| Workflow | Trigger | What it does |
|---|---|---|
| `label-sync.yml` | push to main | Syncs GitHub labels from config |
| `triage.yml` | issue/PR events | Auto-triage new issues |
| `stale.yml` | daily | Mark stale issues/PRs |
| `welcome.yml` | first-time contributor | Welcome message |

### Local CI (experimental)

| Workflow | Trigger | What it validates |
|---|---|---|
| `local-ci.yml` | manual / `act` | Full local CI pipeline via nektos/act |

## Running CI Locally with `act`

```bash
# Install act
curl -s https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash

# Run full CI
make ci

# Run specific job
act push -j backend-unit-tests

# List all jobs
act -l
```

The `.actrc` file configures act with appropriate defaults for Nova's Docker-based dev environment.

## Test Counts (canonical)

| Suite | Count | Command |
|---|---|---|
| Backend unit tests | 6,928 | `cd backend && make test` |
| Frontend unit tests | 681 | `cd frontend && pnpm test` |
| Playwright E2E | 85 | `cd frontend && pnpm test:e2e` |
| Blocking IO gate | 19 | `cd backend && make test-blocking-io` |
| Cross-ref check | 1 | `python3 backend/tests/test_no_cross_references.py` |
