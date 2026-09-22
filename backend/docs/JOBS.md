# Job runner

Background work that must outlive a gateway reload: campaign sends, imports,
long agent tasks, anything on a cron. Added in the 2026-09 upgrade program
(phases P2/P3).

## Shape

```
gateway (FastAPI)            deer-flow-jobs (separate container)
  /api/jobs           ─┐       python -m app.jobs.worker
  /api/v1/admin/jobs   ├─ Postgres tables: jobs, job_events,
  enqueue / read /     │  job_schedules, job_workers
  cancel / retry      ─┘       claim → run handler → outcome
                               reaper · scheduler · self-heartbeat
```

- Engine: `packages/harness/deerflow/jobs/` (queue, context, registry,
  scheduler, worker) + `deerflow/persistence/job/`. Never imports `app.*`.
- Composition: `backend/app/jobs/` — `handlers/` registers job types,
  `worker.py` is the process entrypoint, `healthcheck.py` the container
  probe, `enqueue.py` a CLI (`make jobs-demo`).
- Config: `config.yaml` → `jobs:` (startup-only for the worker; see
  `config.example.yaml`). `jobs.enabled` also drives the UI's feature flag
  through `GET /api/runtime/capabilities` → `features.jobs`.
- Contract: statuses / event types in `contracts/job_status_contract.json`,
  pinned on both sides.

## Semantics

| Concern | Behaviour |
|---|---|
| Enqueue | `type`, `payload`, `queue`, `priority`, `run_after`, `owner_user_id`, `thread_id`, `dedupe_key` (unique while non-terminal), `max_attempts`, `backoff_seconds` |
| Claim | `SELECT … FOR UPDATE SKIP LOCKED` on Postgres; SQLite serialises. Lease with TTL (`lease_ttl_seconds`). |
| Heartbeat | `ctx.heartbeat()` extends the lease and raises `JobCancelled` once a cancel was requested. The worker also heartbeats in the background so a handler that never calls it keeps its lease (but cannot be cancelled mid-flight). |
| Retry | `RetryableError(delay=…)` → exponential backoff ±20 % jitter, cap 1 h, until `max_attempts` → `dead_letter`. Any other exception → `failed`. Unknown job type → `dead_letter` immediately. |
| Reaper | Every `reaper_interval_seconds`: expired leases are retried (or dead-lettered). |
| Cron | `job_schedules` rows; `croniter` in the schedule's timezone; one catch-up run after downtime; dedupe key `sched:<id>:<iso>`. |
| Shutdown | SIGTERM → stop claiming, wait `shutdown_grace_seconds`, release remaining leases (`released` event). |

## Writing a handler

```python
from deerflow.jobs.context import JobContext
from deerflow.jobs.errors import RetryableError


async def send_batch(ctx: JobContext) -> dict:
    for i, item in enumerate(ctx.payload["items"]):
        await ctx.heartbeat()  # cooperation point
        try:
            await deliver(item)
        except TransientError as exc:
            raise RetryableError(str(exc), delay=30) from exc
        await ctx.progress(int(100 * (i + 1) / len(ctx.payload["items"])), f"{i + 1} sent")
    return {"sent": len(ctx.payload["items"])}
```

Register it in `app/jobs/handlers/__init__.py`. Handlers own their owner
checks: a schedule's payload is the schedule owner's, but a handler that
touches user data must verify `ctx.owner_user_id`.

## API

User (`@require_auth`, owner-scoped, 404 for someone else's job):

| Method | Path | Notes |
|---|---|---|
| GET | `/api/jobs?status=&type=&queue=&limit=&offset=` | |
| GET | `/api/jobs/{id}` | |
| GET | `/api/jobs/{id}/events?since_seq=` | JSON poll |
| GET | `/api/jobs/{id}/events/stream` | SSE: `job_event` frames, then `job_done` |
| POST | `/api/jobs/{id}/cancel` | queued/retrying settle at once; running is cooperative |
| POST | `/api/jobs/{id}/retry` | failed / dead_letter → queued |
| GET/POST | `/api/jobs/schedules` | create validates the cron |
| PATCH/DELETE | `/api/jobs/schedules/{id}` | |

Operator (`X-Nova-Ops-Token` or admin session; mutating calls need the CSRF
pair, which nova-ops self-issues):

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/admin/jobs/summary` | counts + workers with `last_seen_age_s` — what the watchdog and the `jobrunner` gate read |
| GET | `/api/v1/admin/jobs` | cross-user listing |
| GET | `/api/v1/admin/jobs/workers` | |
| GET | `/api/v1/admin/jobs/dead-letter` | |
| POST | `/api/v1/admin/jobs/{id}/cancel` · `/retry` | |

## Operations

- Container: `deer-flow-jobs` (compose service `jobs`, reuses the gateway
  image + `.venv` volume, no Docker socket, healthcheck = fresh
  `job_workers` row, autoheal). `NOVA_JOBS_SCALE=0` keeps it off.
- Watchdog: `scripts/healthcheck-daemon.py` probe `P15_jobs_worker` (RED
  without a live heartbeat, YELLOW when dead-letter grows; fixer restarts a
  stopped container). Gate: `scripts/gates/jobs-gate.py` →
  `~/.nova/gates/jobrunner.json`, every 2 min, shown in nova-ops.
- Both read `NOVA_OPS_TOKEN` from the environment or the repo `.env`.
- Smoke test: `make jobs-demo`.
