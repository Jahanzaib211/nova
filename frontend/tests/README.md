# frontend/tests

Test suite for the Nova frontend (Next.js).

## Structure

| Directory | Scope |
|---|---|
| `e2e/` | Playwright E2E tests (mocked backend) |
| `e2e-real-backend/` | Playwright E2E tests against a real gateway |
| `unit/` | Unit and integration tests (Vitest) |

## Running

```bash
cd frontend
pnpm check          # type-check
pnpm test           # unit + integration
pnpm test:e2e       # mocked E2E
```
