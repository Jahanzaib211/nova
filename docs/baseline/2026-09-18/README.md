# Baseline — 2026-09-18

Measured on the live host before the upgrade program (branch
`fix/audit-remediation-2026-09-16`, tree with uncommitted WIP as of that
morning). Every later phase is compared against these numbers; test counts
must not decrease.

| Check | Result |
|---|---|
| Backend pytest (excl. blocking_io) | **6,902 passed, 21 skipped, 1 xfailed** (6m18s, niced, single process) |
| Backend blocking-IO gate | 20 passed |
| Backend lint (`make lint`) | 1 error (`UP041` in uncommitted `tests/test_healthcheck_daemon.py`) — fixed in the same program |
| Frontend `pnpm check` | clean |
| Frontend vitest | **689 passed / 76 files** before this program; 699 after the contract tests |
| pnpm audit | 0 critical / 36 high / 38 moderate / 9 low |
| Gateway OpenAPI | 168 paths, 212 operations → `contracts/openapi.baseline.json` |
| Visual regression | 17 screens × {chromium, mobile-chrome} → `frontend/tests/e2e/visual/__snapshots__/` |
| Machine inventory | `inventory.json` (29 items; only the 88 % disk usage was non-green) |
| Lighthouse | see `lighthouse-summary.json` (performance / accessibility / best-practices / seo medians) |

## How each number was produced

```bash
cd backend && PYTHONPATH=. uv run pytest tests/ -q --ignore=tests/blocking_io
cd backend && make test-blocking-io && make lint
cd frontend && pnpm check && pnpm exec vitest run --reporter=json && pnpm audit --json
cd backend && DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_CONFIG_PATH=../config.example.yaml \
  PYTHONPATH=. uv run python scripts/openapi_snapshot.py > ../contracts/openapi.baseline.json
cd frontend && pnpm build && pnpm start --port 3111   # then:
cd frontend && NOVA_VISUAL=1 E2E_PORT=3111 pnpm exec playwright test tests/e2e/visual \
  --project=chromium --project=mobile-chrome --update-snapshots
python3 scripts/inventory.py --json
```

## What the visual baseline exposed (fixed in the follow-up commits)

- Settings › Account and › Memory crashed the whole settings route on a
  partially-shaped API payload ("Something went wrong").
- Settings › Runtime printed literal `undefined` for missing fields.
- Settings › Models forced the page to 1297 px wide on a phone; the settings
  route had no sidebar trigger on phones; the Tools reload row squeezed to a
  sliver.
- The chat title wrapped to two lines beside an open Agent's Computer; the
  runtime bar crushed "no skills loaded" to zero width on phones.
- A thread with an orphaned `task` call could take the workspace down with
  React #185 under load (subtask registry moved to an external store).
- A collapsed failed subtask card showed only an icon; the Agent's Computer
  default width left a 384 px chat column on a 1280 px laptop.
- e2e infrastructure: `next.config.js` rewrites reached the *live* gateway
  from test builds (401 → `/login` redirects); the voice socket stub captured
  the Agent's Computer socket; two specs still referenced the pre-merge
  "Activity" tab.

Lighthouse (`LHCI_PORT=3210 pnpm lighthouse`, production build, `/` and
`/workspace/chats`) is summarised in `lighthouse-summary.json` via
`scripts/gates/lighthouse-gate.py --json`.
