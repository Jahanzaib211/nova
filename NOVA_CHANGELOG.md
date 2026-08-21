# NOVA_CHANGELOG

> **Canonical forward-looking plan + change log for Nova** (formerly the DeerFlow
> fork; renamed at v4, rename tidy at v5).
>
> **Audience:** the owner (Jahanzaib) + future contributors + the next agent
> that picks up after a token cap. Read this before touching anything.
>
> **What this is NOT:** a port of the upstream DeerFlow changelog
> (`CHANGELOG.md` / `CHANGELOG_zh.md`). Those still describe the base system.

---

## Current Platform Status

> Formerly consolidated from `CONSOLIDATION.md` + `ROADMAP.md` (both absorbed
> into this changelog as of 2026-08-14).

| Phase | Title | Status | Key Deliverables |
|-------|-------|--------|------------------|
| C0 | Foundation (correlation, CI guardrails) | ✅ **complete** (2026-07-12) | Cross-process correlation via `correlation_id`, SSE comment emission + frontend capture, streaming hardening (watchdog, bounded teardown, convergent cleanup), tunnel auto-recovery, CI guardrails (middleware sprawl, duplicate-recovery), `CONSOLIDATION.md` tracker |
| C1 | Documentation sync + typed service layer | ✅ **complete** (2026-07-12) | Repository reality audit (127 files), 12 docs updated + 3 created, 10 Protocol interfaces, 7 typed return models, 10 thin wrapper implementations, 33 unit tests |
| C2 | Run state consolidation + dependency injection | ✅ **complete** (2026-07-12) | `ServiceContainer` with lazy singletons + `override()`, `RunState` frozen dataclass with `from_record()` bridge, gateway wired: `app.state.run_service`, `get_run_service` dependency, backward compat maintained |
| C3 | Unified lifecycle + event bus | ✅ **complete** (2026-07-12) | Canonical `RunLifecycleStatus` enum (11 states), 17 frozen dataclass domain events, `EventBus` (sync, typed, DI-compatible), `EventPublisher`/`Subscriber`/`Registry`, `RunServiceImpl` publishes lifecycle events on create/cancel/set_status, `DiagnosticsServiceImpl` subscribes, `HealthServiceImpl` publishes `HealthChanged` on state transitions, 56 tests |
| C4 | Recovery engine + unified health management | ✅ **complete** (2026-07-12) | `RecoveryEngine` (event-driven via EventBus), 10 declarative policies with `RetryStrategy`, 7 recovery events, 9 default action handlers, recovery history + metrics, cancellation support, 37 tests |
| C5 | Platform Convergence | 🔄 **in progress** | Gateway `list_runs`/`get_run`/`cancel_run` → `RunService`, `create_or_reject()` for multitask-aware runs, worker `_service_set_status()` routes all status transitions through service layer → EventBus receives all lifecycle events, 127 consolidation tests pass |
| C6 | Workspace/Repository abstraction | ⏳ pending | Depends on C2 |
| C7 | Deployment, HA, production hardening | ⏳ pending | Depends on C3–C6 |
| C8 | Performance optimization and scaling | ⏳ pending | Depends on C7 |
| C9 | Product features and extensibility | ✅ **complete** (v9.0) | Accounts (email edit, admin dashboard), terms/consent gate, usage credits + wall, referral flywheel, BYOK, Nova Plus/Stripe billing — see v9.0 below |

**Design Principles:**

1. Single coherent operating system — not a collection of AI features
2. Reduce architectural complexity — every sprint should simplify
3. Increase operational reliability — self-healing, observability, testing
4. Strengthen foundation first — before expanding capabilities
5. Implementation is source of truth — docs follow code, not vice versa

**Constraints:**

- No new user-facing features until consolidated
- No references to the local LLM gateway service name in code
- No references to the model name it wraps in code (allowed only as literal model identifiers in config)
- `deerflow` namespace is allowed (historical)
- TDD mandatory for all new features
- All commits must pass self-test protocol

---

## v9.7 — Room to work, and a sandbox that stops moving

Two problems that turned out to be the same problem: nothing bounded what Nova
wrote to disk, and nothing pinned what Nova ran inside.

### 88 GB back

The disk was at 84% with 73 GB free, which is not enough headroom to add a
toolchain to a 10 GB image. Reclaimed, in order of how safe it was:

- **6.2 GB** — a pre-Postgres SQLite backup dated 2026-07-16.
- **775 MB** — `deerflow.db` and its WAL, superseded by the Postgres migration.
  Verified superseded rather than assumed: Postgres is ahead on every table
  (checkpoints 2,477 vs 1,032; runs 962 vs 941). Kept as a 255 MB compressed
  rollback rather than deleted outright.
- **~11 GB** — three orphan images (`docker-gateway`, `deer-flow-gateway`,
  `deer-flow-frontend`), all `used_by: NONE`, left behind by compose
  **project-name drift**: the running stack is project `deer-flow-dev`, so a
  build run from `docker/` tags `docker-*` instead and orphans the result.
- **49 GB** — regenerable Stremio stream cache.
- **11 GB** — local llama GGUFs, dead since Nova moved to Ollama and the
  llama probes were retired.
- **6.4 GB** — build output in idle thread workspaces (below).

Swap fell from 6.8 GB of 8 to 0.2 GB as a side effect, and the host gate went
green for the first time since it was written.

### Thread workspaces: prune the build output, never the work

Thread workspaces were the other unbounded store — 7.3 GB and no retention, the
same shape as the checkpoint growth that caused the 59 GB outage. But the fix
could not be the same, because the contents are not the same: a workspace holds
the deliverables Nova produced for a user, and deleting those on a timer is
unacceptable.

Measuring first changed the design. **99% of the bytes were regenerable** —
`node_modules`, `.next`, `.venv`. The actual work product was ~90 MB of 7.3 GB.
So `scripts/prune-workspaces.py` never deletes a workspace; it deletes
rebuildable trees inside workspaces idle for `--keep-days`, and a user returning
to an old thread finds their files and reruns `npm install`. Idleness is the
newest mtime anywhere in the tree, not the directory's own — that would have
deleted the dependencies of work still in progress.

Scheduled daily beside the checkpoint pruner, with a `workspace_backlog` check
in the host gate that reports what the pruner *would* remove.

### The sandbox stopped moving

Every sandbox layer built `FROM …/all-in-one-sandbox:latest`. A floating tag
means two builds a week apart can produce two different sandboxes with no change
in this repo and nothing to point at when behaviour drifts. The base is now
pinned by digest (`sha256:742062f9…`), and the single Android Dockerfile is a
chain of four: **base → tools → dind → android**, each one concern, sharing
layers so the intermediate tags cost no disk.

The build was also not reproducible: no `make` target, no CI, and
`setup-sandbox.sh` could only *pull* — so it could not produce
`nova-sandbox-android:latest`, the locally-built tag `config.yaml` had pointed
at for weeks. Now `make sandbox-image` builds the chain, and `setup-sandbox.sh`
builds rather than failing when the configured tag is local.

### The toolchain, reusing the machine

An audit from inside the sandbox found no pandoc, psql, redis-cli, tesseract,
Go, Rust or Playwright. None of it was lost — nobody had added it.

The rule for adding it: reuse what the build host has, download only what it
lacks. The limit is **glibc**. The host is Ubuntu 26.04 (2.43); the image is
22.04 (**2.35**), and glibc is backward- but not forward-compatible, so a host
binary linked against 2.38 copies in, resolves on `PATH`, and dies at exec.
Every candidate was checked with `readelf -V`:

- **Copied** — Go (static), Rust (rustup builds against 2.17 on purpose), `uv`
  (2.17), and the whole Docker engine except one piece (2.34).
- **Downloaded** — pandoc, wkhtmltopdf, tesseract, psql, redis-cli (absent from
  the host too); jq, btop, ninja, dig, tcpdump (present, but 2.38); and `runc`,
  the single gap in the otherwise-copyable DinD stack.

Two things cost nothing: `pnpm` via `corepack`, which ships with Node 22, and
Playwright driving the Chromium already in the base image — about 400 MB saved
and one browser instead of two. Both had been reported missing by the audit.

`build.sh` stages the copyable ones into `vendor/`; when it is empty each step
falls back to a pinned download, so the image still builds on a machine that has
none of this.

Also recorded: the base image ships **three Pythons with divergent package
sets**, and `python3` resolves to the least equipped (3.10 with 171 packages,
against 3.12's 216). `pip install X` then `python3.12 script.py` fails with
`ImportError`. 3.12 is now documented as canonical.

### Docker-in-Docker, and the caps it made necessary

The `dind` layer gives the agent a real daemon, gated behind
`sandbox.privileged` (default **off**) because a privileged container is
effectively host root. `dind-entrypoint.sh` wraps the base image's own
`/opt/gem/run.sh` rather than replacing it, and a daemon that fails to start is
logged and skipped — a sandbox without Docker is still a working sandbox.

Enabling that on this host would have been reckless without limits.
`Committed_AS` was **54.5 GB against a 31.1 GB CommitLimit** — the box promising
175% of the memory it has, after two crashes — and sandbox containers ran with
`--memory 0` and no pids limit. `sandbox.memory_limit` (default `8g`) and
`sandbox.pids_limit` (default `2048`) now bound a runaway build to its own
container.

### Ops console: "not configured" is not an alarm

The Infra tab showed a red *"Could not reach the provisioner's infra API"* on
every Nova that simply has no cluster. The gateway returned 503 for both "you
never configured this" and "your provisioner is down", so the console could not
tell them apart. Unconfigured is now **501** — an absent optional feature — and
renders as a neutral note, while a configured-but-unreachable provisioner keeps
503 and keeps the alarm.


## v9.6 — Agent's Computer: the panel that is Nova's face

**Session pattern:** the agent built a site correctly and the product looked
broken. The Browser tab rendered it as **unstyled HTML** — default-blue links,
no layout — while the same site was perfect in an external browser. The
Terminal tab showed nothing while its backend stream was healthy.

Both were transport/UI faults, not agent faults, which is what makes them
expensive: the work was right and the surface lied about it.

### Browser tab: two proxies, one rewriter

There are two HTTP paths into a sandbox dev server. `_proxy_dev_server` (the
`preview`/`lpreview` path) injects `<base href="{prefix}/">` and runs
`_prefix_html_urls`. `_absproxy_impl` rewrote only the `Location` **header** —
so the document loaded and every `/_next/static/...` request resolved against
the app origin instead of the proxy prefix and 404'd.

That path is not exotic. The Browser tab falls back to absproxy whenever the
canonical preview proxy cannot reach the server, which is the normal case for a
dev server the agent started with raw `bash` rather than `start_dev_server`. The
safety net caught the request and then served it unusably.

Verified against the real broken page: 8 root-absolute assets before, **0
unprefixed after**, `<base>` injected, transformation idempotent.

### Terminal tab: a name list that had rotted twice

Terminal and Activity render a *partition* of one event stream, decided by a
hand-maintained list of tool names. It was missing the entire `shell_*` family
— the AIO-SDK wrappers that are the modern execution path — so an agent working
through `shell_session` put nothing in Terminal and everything in Activity.

The same list had already drifted on 2026-08-14 (`write_file`/`str_replace`/
`read_file` missing, file-only runs left the tab empty). It rots on every tool
addition and it rots **silently**: a misclassified event lands in the other tab
rather than disappearing, so nothing errors.

Now one `isTerminalTool`/`isActivityTool` pair in `core/threads/tool-surface.ts`
with a `shell_` **prefix** rule, so the next member classifies itself.
Extracting it made TypeScript surface **two more consumers** of the stale list —
`files-tab`'s running count and the panel's terminal badge — so the miscount was
wider than the tab itself.

### The capabilities panel was understating Nova by a third

`/api/runtime/capabilities` iterated `BUILTIN_TOOLS` alone despite documenting
itself as "builtin + configured". Live it reported **25 tools where the agent
binds 40**, and the missing half was `bash`, `read_file`, `write_file`, `ls`,
`glob`, `grep`, the web tools, the trading group, `task` and `view_image` —
exactly the entries someone checks to answer "can it run a shell? can it read my
files?". Now built through `get_available_tools()`, the same call the agent uses.

### Two regressions v9.5 introduced, found here

- **The post-migration test suite had never run.** `config.yaml` gained
  `postgres_url: $DATABASE_URL`; that variable is unset in a plain shell, so
  `AppConfig` raised during collection — 8 errors, nothing executed — and the
  pre-migration numbers were reported as if they were post-migration ones. CI
  missed it because CI tests against `config.example.yaml` (sqlite). Setting the
  variable would have been worse: every `TestClient` lifespan would have opened
  the **live production database**. `conftest.py` now hands the suite a
  sqlite-forced copy of the real config.
- **`uv sync --all-packages` pruned the CUDA voice wheels.** They are installed
  by the root Makefile via `uv pip install`, not declared as dependencies, so
  `test_voice_engines_real.py` started failing with `libcublas.so.12 is not
  found`. Restored with `make voice-libs`; all 10 pass.

### Also fixed

- Terminal auto-scroll was ungated on `active`. Tabs stay mounted and are only
  CSS-hidden, so a hidden Terminal dragged a hidden subtree on every event. The
  ttyd iframe stays deliberately ungated — tearing it down would lose the user's
  shell — but that reasoning never extended to effects.
- Terminal events now key on the tool-call id, review files on their path.
  `editor-tab`'s line list keeps index keys, where position genuinely is identity.

### Checked and NOT broken

Recorded so nobody re-investigates: thread-scoped `/api/sandbox/*` returning 404
to a non-owner is `_caller_owns_thread` working, not an outage; `editor-tab` not
taking an `active` prop is fine because that prop is on the tab *button* and
Editor does not poll. The sandbox container still gets no `--gpus` and no
`/dev/dri` while the host has both — a known gap, deliberately not changed.

---

### Two gates that were measuring the wrong number

Both fired on healthy states, which is the failure mode that teaches an owner
to ignore a console.

**`checkpoint_rows` → `checkpoint_backlog`.** The old check averaged checkpoints
over threads. At small thread counts that average describes nothing: one agent
mid-session produced `538 checkpoints across 1 threads` and tripped RED with no
problem behind it. High volume *inside* the retention window is retention
working as designed. The signal that actually means the v9.5 database is coming
back is rows surviving *past* the window, so the check now counts exactly what
the pruner would delete right now — older than `keep_days`, beyond
`keep_per_thread` — on both SQLite and Postgres. Currently 0 of 538.

**`swap` counted zram and the swapfile as one pool.** `/proc/meminfo`'s
`SwapTotal` sums every swap device, so after the v9.5 zram hardening this box's
8 GiB swapfile and 8 GiB zram device were added together. They are not
comparable: zram is RAM-backed and compressed, and 5 GiB of pages there occupy
~1.8 GiB of real memory at no IO cost. The sum reported "11.7 GiB of 16.0 GiB"
for a host whose real pressure was 6.8 GiB of 8 GiB on disk — understating the
number that matters while inventing headroom that does not exist.

Banding on occupancy alone was also wrong. Cold pages of idle services parked in
swap are swap doing its job, and this box legitimately runs a long tail of them
(mysqld, mariadbd, ruby/bundle, an editor, litellm). What preceded the 2026-08-20
freeze was a full swapfile *while pages were moving*. The check now samples
`pswpin`/`pswpout` over 2 s and separates the two: a quiet full swapfile is the
warning it is, and only sustained paging is the emergency it is not yet.


## v9.5 — the 59 GB database behind v9.4, and the gates that make it visible

**Session pattern:** user reported that after recreating the containers on
2026-08-18 "the whole project started regressing, breaking on its own", with
no idea what had changed. The evidence was on the box; nothing was looking
at it.

### The actual root cause

v9.4 correctly identified `database is locked`, the `POST /api/threads` 500s
and the login failures — and fixed them at the wrong layer. The 30 s
`busy_timeout`, `commit_with_lock_retry` and the larger pool are all correct
and stay, but they treat contention, not its cause.

`backend/.deer-flow/data/deerflow.db` had reached **59.4 GB**:

```
checkpoints.checkpoint   56,489 rows  ->  57.3 GB   (avg ~1 MB, largest 58 MB)
writes.value             70,755 rows  ->   1.6 GB
everything else (73 users, 140 threads, 927 runs, 35k audit) -> ~0.5 GB
freelist: 455 pages — the space is LIVE DATA; VACUUM alone reclaims nothing
```

LangGraph's checkpointer serialises the entire accumulated graph state on
every step, and **nothing in the codebase ever deleted a checkpoint** — no
retention, no pruning, no vacuum anywhere under `backend/`. 140 threads
produced 56k checkpoints. Writes to a file that size hold the write lock long
enough that concurrent writers time out; that is what v9.4 was retrying
around. Disk was at 97%.

nginx's own log dates the regression precisely:

| Date | Requests | 200 | 502 | Error rate |
|---|---|---|---|---|
| ≤ 08-15 | — | — | — | ≤ 0.4% |
| 08-16 | 2093 | 23 | 2015 | **98.6%** (the v9.3 outage) |
| 08-17 | 3986 | 184 | 3740 | **95.3%** |
| 08-18 | 184 | 160 | 1 | 0.5% ← the container recreation fixed *that* one |
| 08-19 | 778 | 601 | 63 | **12.6%** ← a second, unrelated regression |

Top failing path: `/api/v1/auth/me`, 5,683 × 502 — which is why login and then
everything else appeared broken.

Pruned to **0.50 GB** (`scripts/prune-checkpoints.py`, keep 3/thread + 2 days).
Disk 97% → 71%. Users, threads and runs untouched; `integrity_check` ok.

### Why it took a day to see

`docker/dev-entrypoint.sh` opened the log with `exec >/app/logs/gateway.log`,
**truncating on every start**. A crash-loop therefore destroyed the record of
the crash that caused it. `logs/gateway.log` held only the last boot. Now
appends, with a dated boot banner, bounded by `scripts/rotate-logs.sh`.

The two are a matched pair: the rotation copy-truncates, which is only safe
because the log is opened `O_APPEND` (the next write lands at offset 0 instead
of recreating a sparse NUL hole). The script detects and reports that NUL-hole
state explicitly — it caught exactly that on a container started before the fix.

### "CI passed" was not true either

`local-ci.yml`'s `backend-format` job ran `make format-check`, **a target that
did not exist**, so `local-ci-gate` — the repo's only aggregate verdict — could
never pass. Underneath it: 10 ruff errors, 7 unformatted files, 3 eslint errors,
59 prettier files, and 1912 markdownlint violations of which 1876 came from one
git-tracked generated file.

One of the eslint errors was real: a **conditional `useEffect`** in
`message-group.tsx` — a tool call's `name` arrives incrementally while it
streams, so the same component rendered once without the hook and again with
it, and React throws "Rendered more hooks than during the previous render"
mid-stream.

### The gates

Four producers now publish machine-readable status to `~/.nova/gates/*.json`,
read by the Nova Ops `/gates` console: **host** (disk, swap, IO pressure, DB
size, checkpoint backlog, and whether the pruner and rotation still run),
**drift** (is what is running what the repo says), **ci** (a ~35 s fast tier
plus a slow tier), and the **watchdog**, which already emitted per-cycle JSON
into a PM2 log nothing read back. Plus Lighthouse budgets and a
`/api/v1/admin/gates/health` endpoint for the gateway's view of its own
dependencies — `HealthServiceImpl` was constructed bare, so `check_all()`
returned `healthy=True, probe_count=0`: a report that could not fail.

`nova-gates` (PM2) keeps them fresh and runs the pruner daily. Without a
supervisor the console showed "Stale — produced 10h ago" on real-but-outdated
numbers, which reads as authoritative and is not.

### Things that turned out not to be true

Worth recording, because each was a plausible reading of real evidence:

- **Compose overlay "drift" was not drift.** `com.docker.compose.project.config_files`
  records a per-service *subset* of the `-f` chain, not the whole chain, so the
  gateway showing `dev,dood,voice` while the frontend shows `dev,dood,prod-frontend`
  is one normal `docker compose up`. Verified by running exactly one.
- **The four SIGABRT'd CI checks were not a memory, io_uring or RLIMIT_NOFILE
  problem.** All three were measured and ruled out. It is CPython's `close_fds`,
  and it must be `False` at *every* level of the spawn chain — one `close_fds=True`
  above a node process makes it abort at teardown, after it has already produced
  correct output.
- **The frontend-build drift check gave two false positives** before it was
  right: mtime and then last-commit time are both rewritten by `git checkout`
  and `git merge` without a byte changing. It now hashes the source baked into
  the image against the working tree.

### Host

The desktop froze with ~2 GB RAM and ~1.4 GB swap still free, load average 30.
Nothing was OOM-killed because nothing reached a kill threshold — a freeze from
thrash happens *before* an OOM event. `earlyoom` was running the whole time
with `-m 6 -s 6`: act below 6% of 30 GiB, i.e. under 1.9 GiB, long after the
machine is unusable. `scripts/host/harden-memory.sh` adds zram as the primary
swap tier (8 G zstd, ~3.7× compression), raises the thresholds to 12%, and sets
a zram-appropriate `vm.swappiness`. Swap 92% → 46%; IO pressure 34.9% → 1.3%.

---

## v9.4 — SQLite lock storm, "Failed to create thread" 500, CDP UX bug, watchdog spam

**Session pattern:** user reported login "network error" and
nova-ops console "Could not reach the Nova gateway"; same hour,
POST /api/threads returned 500 {"detail":"Failed to create thread"}.
Four independent root-cause classes identified in one audit pass and
fixed in five commits (`30747d5e`, `b0097f53`, `bbe22af1`, plus the
v9.3 documentation set already landed at `2b67a6a7`).

### Findings

- **SQLite lock storm.** `logs/gateway.log` carried 318
  `OperationalError: database is locked` tracebacks from concurrent
  ops-console INSERTs into `admin_audit`. Two colluding faults:
  - The aiosqlite `timeout` was at the python `sqlite3` default of
    5 s, far too short for the ops-console's two-per-second polling
    pattern — losers raised immediately. Raised to 30 s
    (`connect_args={"timeout": 30}` + `PRAGMA busy_timeout=30000`).
  - Even at 30 s, two near-simultaneous writes can still collide.
    Added a small 3-attempt exponential-backoff retry helper
    (`deerflow.persistence.engine.commit_with_lock_retry`) shared
    across every SQL write site.
- **`Failed to create thread` 500.** `POST /api/threads` returned
  HTTP 500 from the same lock contention: `ThreadMetaRepository.create()`
  bypassed the retry helper. After the bbe22af1 fix landed, this was
  the highest-visibility failure mode — every chat page hit it during
  the gateway's first reload. Fixed by promoting the helper into
  `deerflow.persistence.engine` and wrapping every
  `ThreadMetaRepository` write site (`create`,
  `update_display_name`, `update_status`, `update_metadata`,
  `update_owner`, `delete`).
- **CDP `false` UX bug.** `/api/health/browser` reported
  `cdp_reachable: false` even when no thread was using a sandbox —
  the probe returned `(False, None, None)` for "nothing to probe"
  and the dashboard rendered it as broken. Now returns
  `(None, None, None)` (tri-state distinct from "ran and failed")
  and the response adds `reason: no_active_thread | probe_failed | ok`
  plus `cdp_url`.
- **Watchdog P4/P5 spam.** P4_local_llm_gateway and P5_llama_loopback
  had been RED for 9 300+ cycles (~77 h) with "no auto-fix registered"
  warnings every 10 min. The local LLM stack is intentionally not
  wired on this deployment (SESSION-HANDOFF §3.G4). Added both names
  to `HEALTHCHECK_DISABLED_PROBES` in `ecosystem.config.js`. P6
  (`llama_vram`) stays enabled because it already self-masks as
  YELLOW "no model advertised".

### Live verification (after the restart at 20:45 PKT)

- Backend suite: **6427 passed** (only known-red
  `test_sandbox_orphan_reconciliation_e2e` failed — races on shared
  Docker state, pre-existing, not code).
- `curl https://nova.alilabsx.com/api/v1/admin/credit-requests`:
  200 OK in **18 ms** (was hanging 10 s and timing out at the
  nova-ops client).
- `curl http://localhost:2026/api/health/browser`: `reason:
  "no_active_thread"` instead of `cdp_reachable: false`.
- `nova-healthcheck` cycle 47: P1/P2/P3/P7/P8/P10 all green;
  P4/P5/P9/P11/P12 properly skipped; P6 yellow as expected.
- 0 new `database is locked` errors in the 5 min after restart.

---

## v9.3 — gateway outage: config-path leak, inotify exhaustion, and a production stack

**Session pattern:** an outage on `nova.alilabsx.com` traced to two independent
faults in the gateway container, then the structural fix that removes the class.

### The outage (2026-08-17)

`deer-flow-gateway` was crash-looping; nginx logged
`gateway could not be resolved (2: Server failure)` — Docker's embedded DNS
SERVFAILing the name of a container that is down, which reads like a DNS fault
and is not one. The tunnel and nginx were healthy throughout
(`readyConnections:4`, nginx answering 200 on :2026).

**`docker logs deer-flow-gateway` was empty**, which is why this was slow to see:
`docker/dev-entrypoint.sh` does `exec >/app/logs/gateway.log 2>&1`. Two unrelated
failures were sitting in that file.

- **Config-path leak (fatal).** The container's `DEER_FLOW_CONFIG_PATH` was a
  *host* path, so `AppConfig.resolve_config_path()` raised `FileNotFoundError` at
  import inside `create_app()` and uvicorn never bound 8001. Cause: the repo-root
  `.env` legitimately holds host paths — `docker-compose.yaml` uses
  `${DEER_FLOW_CONFIG_PATH}` as a bind-mount *source* — and
  `docker-compose-dev.yaml` pinned `DEER_FLOW_PROJECT_ROOT`/`DEER_FLOW_HOME` in
  its `environment:` block but not the two config-path vars, so `env_file:` leaked
  them straight through. `docker-compose.yaml` had always pinned them; this was a
  pure asymmetry between the two files. Note the inverse of the v9.2 note at the
  "Environment leakage" entry below: `.env` used to hold `/app/...` paths and was
  later flipped to host paths, which is correct — but it moves the burden onto
  every compose service to override them.
- **inotify exhaustion (secondary).** The uvicorn `--reload` supervisor died with
  `WatchfilesRustInternalError: ... Too many open files (os error 24)` —
  `fs.inotify.max_user_instances` at the Ubuntu default of 128, exhausted across
  ~25 containers and ~30 PM2 processes. Because the supervisor is PID 1, its
  death *exited the container*, converting a recoverable fault into a crash-loop.

### Fixes

- `docker/docker-compose-dev.yaml` — pin `DEER_FLOW_CONFIG_PATH=/app/config.yaml`
  and `DEER_FLOW_EXTENSIONS_CONFIG_PATH=/app/extensions_config.json` in the
  gateway's `environment:`.
- `/etc/sysctl.d/60-nova-inotify.conf` — `max_user_instances=1024`,
  `max_user_watches=524288` (host change, not in-repo).
- **`docker/docker-compose.nova-prod.yaml` (new)** — a standalone production
  stack: final image (venv baked in, no boot-time `uv sync`), **no reload
  watcher**, non-root, config mounted read-only, nginx still loopback-only, and
  autoheal retained. It is *standalone rather than an overlay* because Compose
  merges `volumes:` by appending — an overlay can override `command:` and
  `build.target:` but can never remove the dev stack's `.venv`/source bind mounts.
  `docker-compose.prod-frontend.yaml` demonstrates the limit: the merged frontend
  runs the prod target yet still carries dev `frontend/src` mounts (inert there,
  fatal for the gateway).
- `scripts/pm2-deerflow.sh` — `NOVA_STACK=dev|prod` selects the stack; `dev`
  remains the default and emits a byte-identical command to before.
- `backend/Dockerfile` + **`docker/uv-sync-extras.sh` (new)** — `UV_EXTRAS` now
  accepts a comma/whitespace list. It was interpolated as `--extra $UV_EXTRAS`,
  so any multi-extra value produced the invalid flag `--extra trading,voice`;
  only single-extra builds had ever worked, which would have silently dropped the
  trading tools and voice engines from the prod image. Extracted to a script
  because a shell loop cannot be written safely inline in a `RUN`: the Dockerfile
  parser expands unknown variables to empty, and escaping them as `\$name` passes
  the backslash to `/bin/sh`, where `\$(...)` is a syntax error. Also adds
  `--all-packages`, without which the harness's own `trading`/`voice` extras are
  skipped. `.dockerignore` gains an exception so the script reaches the context.

### Docs corrected

Several docs were wrong in ways that actively cost time during the outage:

- `docs/TROUBLESHOOTING.md` — "Gateway won't start" pointed at `docker logs`
  (empty by design). Now leads with `logs/gateway.log` and carries a
  symptom→cause table for both faults above.
- `scripts/docker.sh logs --gateway` — was `docker compose logs gateway`, i.e.
  the same empty stream; now follows the real file when it exists.
- `CONTRIBUTING.md` — documented `make docker-logs-frontend` /
  `make docker-logs-gateway`; **neither target exists**. Correct form is
  `make docker-logs ARGS=--gateway`.
- `.opencode/skill/nova-agent/{SKILL.md,references/diagnose.md}` — the only
  documented gateway crash-loop was a Docker *name conflict*, whose fix
  (`down --remove-orphans` + pm2 restart) would have recreated the container with
  the same broken env. Split into two variants keyed on
  `docker ps -a` status. Also corrected the PM2 process name (`nova`, not
  `deerflow`) and dropped a health check against `localhost:8000` (the gateway
  publishes no host port at all).
- `docs/ops/SESSION-HANDOFF.md` — stated `.env` exports a *container* path;
  inverted since the file changed on 2026-08-15.
- `docs/RUNBOOK.md` §7 — monitoring table cited `cloudflared-nova.service` and
  `systemctl is-active cloudflared-nova`. **No cloudflared systemd unit and no
  `/etc/cloudflared/` exist on this host**; the tunnels are PM2. Replaced with
  the PM2 process names and the `/ready` metrics probe.
- `backend/docs/CONFIGURATION.md` — had no coverage of the `environment:` vs
  `env_file:` rule at all; new "Docker: config paths vs `env_file`" section. Also
  fixed rebrand drift: the page documented `NOVA_CONFIG_PATH` /
  `NOVA_PROJECT_ROOT`, **names that appear nowhere in the codebase**.
- Config-priority lists in `backend/CLAUDE.md`, `DEVELOPMENT.md`,
  `backend/README.md` now note the strictness of levels 1–2 and the Docker rule.

---

## v9.2 — full-stack audit: security, accessibility, robustness, and the Runtime config UI

**Session pattern:** one big sweep across both apps to close security gaps, harden error handling, improve accessibility, and ship the new "Runtime" settings surface.

### Features

- **Runtime config UI** — new `Runtime` section in Settings (modal and `/settings` page). Read-only view of summarization, subagents, and guardrails settings, backed by the new `GET /api/runtime/config` endpoint. Editing requires `config.yaml` (deferred to a future write endpoint — file locking + schema validation + graceful hot-reload are out of scope for this surface).
- **`GET /api/runtime/config`** — new router (`app/gateway/routers/runtime.py`) returning a Pydantic summary of the three operator-facing sections. Safe-get traversal means missing fields return defaults rather than 500s.

### Security

- **Sandbox IDOR fix (C1-C3)** — five `/api/sandbox/{logs,status,file,files,download-zip}` endpoints now verify thread ownership via `_caller_owns_thread()` and return 404 on mismatch (was returning empty data to any authenticated caller). Three more endpoints (`/todo`, `/dev-status`, `/dev-servers`) were returning empty JSON instead of 404 — now properly 404.
- **Share token entropy (M1)** — `sharing.py` switched from `uuid.uuid4().hex` (128 bits, predictable structure) to `secrets.token_urlsafe(32)` (~256 bits, URL-safe base64). Token length in tests relaxed from 32 → 43 chars.
- **Trusted-proxy XFF (M2)** — `auth_rate_limit_middleware._client_ip` now only honors `X-Forwarded-For` when the TCP peer matches `AUTH_TRUSTED_PROXIES` (CIDR list). Direct clients can no longer spoof their IP to bypass rate limits. Mirrors the contract already enforced by `app.gateway.auth.client_meta.get_client_ip`. 9 new tests in `test_auth_rate_limit_trusted_proxies.py`.
- **Channel bare `except Exception` (H5)** — narrowed 9 bare `pass` blocks to `except ImportError` (where they belong) or added `logger.debug()` in 4 feishu/slack/discord/wecom sites. Audit trail improvement; nothing should change behaviorally.

### Robustness

- **`_bridge_ws` (H1)** — websocket bridge in sandbox router now logs exceptions rather than silently dropping them.
- **`_is_port_open` (H2)** — wrapped in `asyncio.to_thread` to keep blocking IO off the event loop.
- **Frontend error handling** — `api.ts` distinguishes 403/404 from 5xx (4 functions now throw on unexpected errors); `artifacts/loader.ts` checks `response.ok`; `credits-meter.tsx` shows "Credits information unavailable" on error; `billing-settings.tsx` has try/catch around redirect; `terms-gate.tsx` has `role="dialog"` + `aria-modal` + `aria-label`.
- **Error boundary** — chat page `<main>` wrapped in `<ErrorBoundary scope="chat-main">`.

### Accessibility

- **`credits-meter.tsx` progress bar (M9)** — added `role="progressbar"`, `aria-valuenow`, `aria-valuemin/max`, `aria-label`, `aria-valuetext`.
- **`todo-list.tsx` live region (M10)** — added `role="status"` + `aria-live="polite"` sr-only region announcing todo count + any in-progress task.
- **`auth-form.tsx` (M12)** — explicit `aria-live="assertive"` on errors and `aria-live="polite"` on success.
- **`loader.tsx`** — added `role="status"` + `aria-label="Loading"` from P4 (carried forward).

### UX

- **Share dialog (M11)** — explicit "Create share link" confirmation button before invoking `useCreateShareLink`. Previously the link was created immediately on dialog open with no chance to cancel.
- **`React.memo` on `RecentChatList` and `TokenUsageIndicator` (M8)** — renamed inner functions with `_` suffix (matches the existing `message-list-item.tsx` pattern); prevents re-renders when parent state changes.

### Tests

- **10 new sandbox endpoint tests** in `test_sandbox_endpoints.py` covering `/logs`, `/status`, `/file`, `/files` including ownership-check enforcement on every endpoint.
- **9 new trusted-proxy tests** in `test_auth_rate_limit_trusted_proxies.py` covering XFF allowed/denied paths, CIDR parsing, invalid entries.
- **6 new runtime config tests** in `test_runtime_config_router.py` covering defaults, summarization trigger/model, subagents custom count, guardrails provider class, missing/None fields.

### Skipped (documented)

- **M3** — `RunStore.update_run_progress` noop is the documented contract (pinned by `test_update_run_progress_defaults_to_noop_for_custom_store`), not a bug.
- **M4** — `ws_same_origin` already correct.
- **M5** — WeCom stubs out of scope.
- **M6** — `todo-list.tsx` add/delete/complete not in scope; the component is display-only and owned by the agent.
- **M7** — `about-content.md` i18n is a larger translation effort.

### Gate summary

| | Before | After |
|---|---|---|
| Backend tests | 6430 | 6455 |
| Frontend tests | 565 | 565 |
| New tests | — | 25 |
| Backend lint | clean | clean |
| Frontend lint | 3 pre-existing errors | 2 pre-existing errors (fixed 1 from P5) |

---

## v9.1 — ops conversation visibility + Android sandbox toolchain

**Session pattern:** two independent operator-facing gaps closed together: the ops console had no way to read a user's actual conversation content (only usage/billing metadata), and the agent sandbox had no Android build toolchain, so it could write Kotlin/Gradle Android projects it could not compile.

### Features

- **Admin conversation content** — `GET /api/v1/admin/users/{id}/conversations` (list a user's threads) and `GET /api/v1/admin/users/{id}/conversations/{thread_id}/messages` (full message content for one thread), both gated by `require_admin_user`. The messages endpoint verifies the thread actually belongs to the target user first (404 otherwise), so an admin cannot read another user's messages by guessing thread ids. Built directly on `ThreadMetaRepository` + `DbRunEventStore` via `get_session_factory()` (same SQL-only pattern as the rest of `admin_ops.py`) rather than the app.state-managed store abstractions, so both endpoints require a SQL-backed `database.backend` (503 in memory mode). Every read is written to the audit trail (`view-conversations` / `view-conversation-messages`) since this is the one admin surface that exposes private message text.
- **Android-capable sandbox image** — `docker/sandbox/Dockerfile.android` extends the default AIO sandbox image (`enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest`, Ubuntu 22.04) with OpenJDK 17, Android SDK cmdline-tools + platform-tools + build-tools;34.0.0 + platforms;android-34, Gradle 8.7, and the Kotlin 1.9.24 compiler. Build-verified locally (`docker build` + `java`/`sdkmanager`/`gradle`/`kotlinc`/`adb` all resolve inside the built image). The emulator is deliberately not installed — it needs KVM hardware acceleration a plain Docker container doesn't have. See `docker/sandbox/README.md`.

### Tests

`test_admin_users.py` gained 5 tests for the new conversation endpoints (thread listing, message content, cross-user 404, regular-user 403, audit trail). Full backend suite green (5832 passed) except the pre-existing `test_client_live.py` live-LLM test, unrelated (Fireworks.ai account suspended for billing, not a code issue).

---

## v9.0 — Phase C9: accounts, monetization & the referral flywheel

**Session pattern:** greenfield product layer on top of the C-phase platform — accounts, usage credits, referrals, and paid billing. All additive; every account column backfills the 24 existing users.

### Data model (migrations)

- **`2026_07_15_nova_plus_user_columns`** — adds to `users`: `plan` (free\|plus\|enterprise, default free), `plan_status`, `plan_renews_at`, `stripe_customer_id`, `stripe_subscription_id`, `tos_accepted_version`, `tos_accepted_at`, `referral_code` (unique), `referred_by`.
- **`2026_07_15_credit_grants`** — `credit_grants` table (time-limited daily token bonuses; backs referral boosts).
- **`2026_07_15_user_api_keys`** — `user_api_keys` table (Fernet-encrypted BYOK keys).
- All idempotent; verified against a copy of the live DB (24 users → all `plan=free`, no data loss).

### Features

- **Account/email edit** — `POST /api/v1/auth/update-email` (re-auth + uniqueness + token_version bump, no forced password change). Account settings gains an email-edit form.
- **Admin signups dashboard** — `GET /api/v1/admin/users` + `/users/stats` (gated by `require_admin_user`); admin-only "Users" settings section shows count, growth, and the roster (this is where the 24 emails surface).
- **Terms & consent** — `/terms` + `/privacy` pages, footer links, signup acceptance checkbox, `GET /api/v1/legal/terms` + `POST /api/v1/legal/accept`, and a blocking re-acceptance gate in the workspace when `tos_accepted_version` is stale.
- **Nova credits** — 250k tokens/day free (5M plus, 50M enterprise), computed from the `runs` table (no parallel counter). Wall enforced in `start_run` (402 for exhausted end users; admins/internal/BYOK exempt, fail-open when the engine is unavailable). `GET /api/v1/credits` + account meter.
- **Referral flywheel** — per-user invite code, `?ref=` capture at signup, double-sided grants (new user +250k/day×7d → 500k/day; referrer +100k/day×30d). `GET /api/v1/referral` + account referral card.
- **Bring-your-own-key** — Fernet-encrypted per-user LLM key, `GET/POST/DELETE /api/v1/byok`, injected into the run via a task-local contextvar the model factory reads (`deerflow.runtime.byok_context`), bypasses the wall. **Default OFF** behind `NOVA_BYOK_ENABLED` + `NOVA_BYOK_SECRET`.
- **Nova Plus / Stripe** — `POST /api/v1/billing/checkout` + `/portal`, signature-verified `/webhook` (auth+CSRF exempt) driving plan state, admin manual grant `PATCH /api/v1/admin/users/{id}/plan`. **Default OFF** until `STRIPE_SECRET_KEY` (+ `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_PLUS`) are set. `stripe==15.3.0` added.

### Tests

New suites: `test_update_email`, `test_admin_users`, `test_legal_consent`, `test_credits`, `test_referrals`, `test_byok`, `test_billing` (+ foundation round-trip in `test_auth`). Harness→app boundary still green.

### Operator prerequisites (before enabling the paid path)

1. Provision Stripe (product + price) and set `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_PRICE_ID_PLUS`; point a Stripe webhook at `/api/v1/billing/webhook`.
2. For BYOK: set `NOVA_BYOK_SECRET=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")` and `NOVA_BYOK_ENABLED=1`, then verify a live run uses the user's key before announcing.

---

## v8.0 — Phase C0: foundation + streaming hardening

**Session pattern:** forensic production investigation → targeted hardening → consolidation tracking.

### Production incident (2026-07-12)

- **HTTP 500 regression:** Phase C0 added `correlation_id` to `RunRow` ORM model but created no Alembic migration. SQLAlchemy's `Base.metadata.create_all()` only creates tables, not columns — live SQLite DB had 24 columns while ORM expected 25. Every new run creation failed with `OperationalError: no such column: runs.correlation_id`.
- **Fix:** `ALTER TABLE runs ADD COLUMN correlation_id VARCHAR(64)` inside running gateway container + new idempotent Alembic migration `2026_07_12_phase_c0_correlation_id.py` + `env.py` updated with `DEER_FLOW_DATABASE_URL` env-var override.

### Streaming hardening (Phases 1–6)

- **Phase 1:** Watchdog with 12 probes (P1–P12) covering nginx, gateway, frontend, local LLM stack, containers, binary attestation, and Cloudflare tunnel.
- **Phase 2:** Bounded Stop + Force Disconnect — stop is a state machine with configurable timeout.
- **Phase 3:** Active-run polling — never fully disabled while a run exists.
- **Phase 4:** Convergent teardown — deterministic cleanup on disconnect.
- **Phase 5:** Backend store-only cancel — cancel persists through RunStore, startup reaper cleans stale runs.
- **Phase 6:** Tunnel auto-recovery — `fix_tunnel()` calls `systemctl reset-failed` before restart, verifies `is-active` for up to 10s post-restart. PM2 lock path moved to `$XDG_RUNTIME_DIR`.

### Cross-process correlation (Phase C0)

- **Backend:** `RunRecord.correlation_id` field, SSE comment emission (`: correlation_id=<hex>`), `RunManager.create()` generates UUID hex.
- **Frontend:** `stream-liveness.ts` captures correlation_id from SSE comments, `stream-trace.ts` propagates to `DiagnosticsRecord`.
- **Diagnostics:** `register_correlation_id()` / `lookup_correlation_id()` thread-scope helpers, module-level registry.
- **Tests:** 29 backend streaming/correlation tests, 13 frontend correlation capture + round-trip tests.

### CI guardrails

- `scripts/check_platform_guardrails.py` — middleware sprawl check (baseline: 28, allowed: 29) + duplicate-recovery detector (canonical site: `hooks.ts`).
- Guardrails run on every commit via AGENTS.md self-test protocol.

### Consolidation tracking

- `CONSOLIDATION.md` established as single source of truth for platform consolidation program.
- Directives 1–10 tracked with phases C0–C5.

### Tests

- 457 frontend unit tests (49 files).
- 29 backend streaming/correlation tests.
- 5 tunnel recovery tests.
- Cross-ref check passes (1497 files).

---

## v8.1 — Phase C1: documentation sync + typed service layer

**Session pattern:** repository audit → doc synchronization → service layer foundation.

### C1.1 — Repository Reality Audit

- 127 markdown files audited. 12 stale documents. 3 missing.
- Key corrections: README probe count (11→12), MONITORING Loki version (3.5.0→3.6.12), MONITORING Uptime Kuma port (3001→3003), ARCHITECTURE middleware count (8→19).

### C1.2 — Documentation Synchronization

- 12 files updated: README.md, CONSOLIDATION.md, NOVA_CHANGELOG.md, backend/CLAUDE.md, backend/docs/ARCHITECTURE.md, docs/RUNBOOK.md, docs/MONITORING.md, CONTRIBUTING.md, backend/CONTRIBUTING.md, frontend/CLAUDE.md, CHANGELOG.md.
- 3 new files created: DEPLOYMENT.md, DEVELOPMENT.md, ROADMAP.md.

### C1.3 — Typed Service Foundation

- 10 Protocol interfaces (`services/protocols.py`): RunService, WorkspaceService, RepositoryService, BrowserService, TerminalService, ArtifactService, HealthService, RecoveryService, ConfigurationService, DiagnosticsService.
- 7 typed return models (`services/types.py`): RunState, RunSummary, RunDetail, ProbeResult, HealthReport, RecoveryAction, DiagnosticsRecord, WorkspacePaths.
- 10 thin wrapper implementations (`services/implementations.py`).
- 33 unit tests (`tests/test_service_layer.py`).

---

## v8.2 — Phase C2: run state consolidation + dependency injection

**Session pattern:** DI container → canonical RunState → gateway wiring.

### C2.1 — Dependency Injection Container

- `ServiceContainer` class (`services/container.py`): lazy singletons, `override()` for testing, module-level `service_container` singleton.
- Gateway wired: `app.state.run_service`, `get_run_service` FastAPI dependency.

### C2.2 — Canonical RunState

- `RunState` frozen dataclass (`services/types.py`): immutable runtime object with `from_record()` bridge for backward compatibility.

### C2.3 — Gateway Wiring

- `app/gateway/deps.py`: container wired with gateway-owned singletons after RunManager construction. `get_run_service` FastAPI dependency added. `app.state.run_service` exposed.
- Backward compat: `get_run_manager` still works unchanged.

### Tests

- 33 service layer tests, all pass.
- 56/56 backend tests, 457/457 frontend tests.
- Guardrails 28/28, cross-ref clean.

---

## v8.3 — Phase C3: unified lifecycle + event bus

**Session pattern:** enum audit → lifecycle model → event bus → service integration.

### C3.1 — Lifecycle Model

- `RunLifecycleStatus` enum (`runtime/lifecycle.py`): 11 states (CREATED, INITIALIZING, RUNNING, CHECKPOINT, PAUSED, RESUMED, RECOVERING, COMPLETED, FAILED, CANCELLED, ARCHIVED).
- Properties: `is_terminal`, `is_active`, `is_transitional`.
- Adapters: `adapt_run_status()`, `to_run_status()` for backward compatibility.

### C3.2 — Event Bus

- `EventBus` (`events/bus.py`): synchronous, typed, deterministic, DI-compatible. Module-level `event_bus` singleton.
- 17 frozen dataclass domain events (`events/event.py`): RunCreated through RunArchived, WorkspaceMounted/Released, BrowserStarted/Stopped, HealthChanged, ToolExecuted, ArtifactCreated.
- `EventPublisher` (`events/publisher.py`): automatic metadata injection.
- `EventSubscriber` (`events/subscriber.py`): decorator-based registration.
- `EventRegistry` (`events/registry.py`): event type discovery by category.

### C3.3 — Service Integration

- `RunServiceImpl` publishes lifecycle events on `create()`, `cancel()`, and `set_status()`.
- `DiagnosticsServiceImpl.subscribe_to_events()` records all domain events as diagnostics.
- `HealthServiceImpl` publishes `HealthChanged` on state transitions (deduplicated on steady state).

### Tests

- 56 new tests (`tests/test_event_bus.py`), all pass.
- 33 service layer tests, all pass.
- 56/56 backend tests, 457/457 frontend tests.
- Guardrails 28/28, cross-ref clean.

---

## v8.4 — Phase C4: recovery engine + unified health management

**Session pattern:** repository audit → declarative policies → event-driven recovery engine → service integration.

### C4.1 — Repository Audit

- 10+ distinct recovery implementations identified:
  - `fix_tunnel()` — cloudflared restart with reset-failed
  - `fix_llama_bridge()` — PM2 restart or re-register
  - `fix_litellm()` — PM2 restart or re-register
  - `fix_dify()` — PM2 restart or re-register
  - `fix_deerflow_containers()` — PM2 restart
  - `RecoveryServiceImpl.recover()` — wraps fix_tunnel
  - `llm_error_handling_middleware` — exponential backoff + circuit breaker
  - `browser_retry` — bounded retry + circuit breaker + jitter
  - `reap_orphaned_runs()` — Phase 6 startup reaper
  - `_reconcile_orphans()` — Docker/k8s container adoption
  - `recordRecovery()` — frontend trace recorder

### C4.2 — Recovery Events

- 7 new frozen dataclass recovery events: RecoveryStarted, RecoveryRetryScheduled, RecoverySucceeded, RecoveryFailed, RecoveryEscalated, RecoveryCancelled, RecoveryAborted.
- Events registered under "recovery" category in EventRegistry.

### C4.3 — Declarative Recovery Policies

- `RecoveryPolicy` + `RetryStrategy` dataclasses (`services/recovery_policy.py`).
- 10 policies: TUNNEL_DISCONNECTED, GATEWAY_UNAVAILABLE, STREAM_STALLED, BROWSER_DISCONNECTED, BROWSER_CRASH, SANDBOX_UNAVAILABLE, HEALTH_DEGRADED, CONTAINER_RESTART, ORPHAN_RUN, WORKER_EXITED.
- `select_policy()`, `policies_for_trigger()`, `all_policies()`.

### C4.4 — Recovery Engine

- `RecoveryEngine` (`services/recovery_service.py`): event-driven orchestration, retry/backoff, cancellation, history, metrics.
- 9 default action handlers wrapping existing implementations.
- `ServiceContainer.recovery_engine()` singleton.

### Tests

- 37 new tests (`tests/test_recovery_engine.py`), all pass.
- 56 event bus tests, 33 service layer tests, all pass.
- 457/457 frontend tests, guardrails 28/28, cross-ref clean.

---

## v8.5 — Phase C5: platform convergence

**Session pattern:** repository audit → service migration → event-driven convergence.

### C5.1 — Repository Audit (complete)

- Comprehensive audit of remaining direct calls across the codebase:
  - `get_app_config()`: 33 files, ~95 call sites (wrapper exists via `ConfigurationService`)
  - `RunManager` direct imports: 7 files, ~23 call sites (gateway layer)
  - `RunStatus` direct imports: 8 files, ~57 call sites (internal to runtime)
  - `diagnostics.record`: 2 files, 9 call sites (module-internal)
  - `RunRepository` direct imports: 3 files, 9 call sites (initialization only)

### C5.2 — Service Migration: Gateway → RunService (complete)

- Added `create_or_reject()` to `RunService` protocol and `RunServiceImpl` for multitask-aware run creation.
- Migrated gateway endpoints to use `RunService`:
  - `list_runs()` → `RunService.list_by_thread()`
  - `get_run()` → `RunService.get()`
  - `cancel_run()` → `RunService.cancel()` (RunManager retained for wait=True task await)
- Added `_service_to_response()` helper for RunDetail/RunSummary → RunResponse conversion.
- Added `test_create_or_reject_delegates` test (34 service layer tests, up from 33).

### C5.3 — Event-Driven Convergence: Worker Status Routing (complete)

- **Problem:** `worker.py` called `RunManager.set_status()` directly, bypassing `RunServiceImpl.set_status()` which publishes lifecycle events to the EventBus. Status transitions (running → success/error/interrupted) were invisible to event subscribers.
- **Fix:** Added `_service_set_status()` helper in worker that routes through `RunServiceImpl.set_status()` with fallback to `RunManager.set_status()`. All 9 `run_manager.set_status()` calls replaced.
- **Impact:** All run lifecycle transitions now emit domain events (RunStarted, RunCompleted, RunFailed, RunCancelled, RunInterrupted) through the EventBus, visible to DiagnosticsServiceImpl and any future subscribers.

### Tests

- 127 consolidation tests pass (34 service + 56 event bus + 37 recovery engine).
- Cross-ref check clean.
- Zero API changes, zero runtime regressions.

---

## v8.7 — Phase C7: execution kernel

**Session pattern:** repository execution audit → kernel implementation → call-site migration → guardrail enforcement.

### C7.1 — Repository Execution Audit

- 103 raw execution-primitive hits; 6 backend production files migrated, ops/dev scripts classified as out-of-process (documented debt):
  - `services/recovery_service.py` (pm2 restarts), `sandbox/review.py` (git), `sandbox/local/local_sandbox.py` (shell), `sandbox/dev_server.py` (long-running spawn + TERM/KILL), `community/aio_sandbox/local_backend.py` (8 docker/apple-container CLI sites).
- No browser process launches found — all browser work is CDP connects to the AIO chromium; modeled as audited session acquisition, not spawn.

### C7.2 — Execution Kernel (`deerflow/execution/`)

- One sanctioned `subprocess.Popen` site (`kernel.py`); everything else builds typed `ExecutionRequest`s.
- Pipeline: Scheduler (PolicyEngine + ResourceManager) → supervised process → typed `ExecutionResult` → hash-chained AuditEngine → ExecutionMetrics → domain events.
- Per-class policies: program allow-lists (git/docker/pm2/systemctl), sudo gated to `sudo -n systemctl` only, timeout clamps; `shell=True` impossible by construction (argv-only requests).
- Supervisor: process registry, process-group TERM→grace→KILL (kernel processes are session leaders so `sh -c` grandchildren die too), cancellation by execution id, orphan reconciliation wired into gateway lifespan shutdown.
- ReplayEngine: `dry_run`/`replay` of any audited execution; audit records never store env values (key names only).
- `kernel.spawn()` for supervised long-running processes (dev servers) with streaming stdout and `terminate_gracefully()`.
- Adapters: Shell, Docker (docker + Apple Container), Git, Browser (CDP sessions), Python, PM2, Systemd. `FakeExecutionKernel` test double in `deerflow.execution.testing`.

### C7.3 — Platform integration

- 9 new domain events (ExecutionRequested/Started/Completed/Failed/TimedOut/Cancelled/Denied, ProcessSpawned/Exited) registered under `execution` category.
- DI: `service_container.execution_kernel()` singleton; gateway wires `app.state.execution_kernel` + `get_execution_kernel` dependency; kernel shares the global EventBus.
- Recovery engine actions (`_recover_gateway`, `_recover_container`) route through Pm2Adapter; `_recover_tunnel` reimplemented on SystemdAdapter (reset-failed → restart → verify is-active ≤10s), removing the in-gateway import of the healthcheck daemon.

### C7.4 — Guardrails + tests

- `tests/test_execution_guardrails.py`: CI fails if any direct execution primitive appears in `backend/packages` or `backend/app` outside `deerflow/execution/`.
- 36 kernel tests (policy denial, sudo gating, timeout escalation, resource saturation → typed DENIED, audit chain verification, replay, cancel, spawn lifecycle, adapters, DI).
- Migrated test suites off `subprocess.run` monkeypatching onto `FakeExecutionKernel` overrides.
- Full backend suite green except one pre-existing environmental failure (`test_amd_usage_endpoint_reports_backed_models` expects `/app/extensions_config.json`; fails identically on the unmodified tree).
- Docs: `backend/docs/EXECUTION_KERNEL.md`.

### Known debt (Phase C8 candidates)

- `scripts/healthcheck-daemon.py` stays out-of-process by design (watchdog of last resort under pm2) and keeps its own subprocess calls. The in-gateway recovery paths that used to import from it now go through the kernel's PM2/Systemd adapters.
- `aio_sandbox_provider.py` signal handlers (cleanup registration) not yet owned by the supervisor.
- Playwright CDP connect sites in `workspace_tools.py` / `browser_check.py` can adopt `BrowserAdapter.session()`.
- Audit trail is in-memory (10k ring); persistence to the diagnostics store not yet wired.

### C7.5 — Suite-health forensics (pre-existing failures, all root-caused and fixed)

The full backend suite had been hanging at ~47% and carrying 41 pre-existing failures (verified identical on the unmodified C6 tree). All fixed:

- **Deadlock (suite hang):** `mcp/session_pool.py::_run_session` reflected only `Exception` into the `ready` future — when `close_all()` **cancelled** an in-flight owner task (`CancelledError` is a `BaseException`), `ready` stayed pending forever and the caller blocked on `await asyncio.shield(ready)` (`ep_poll` forever). Fix: cancelled owners now cancel `ready`; get_session Phase 3 unwind catches `BaseException` so caller-cancellation cleanup (documented "case 2", previously dead code) actually runs. Fixes `test_close_all_during_in_flight_creation_does_not_resurrect_session` (the hang), `test_get_session_cancelled_while_initializing_does_not_leak`, and `test_cross_loop_preempting_blocked_in_flight_does_not_hang_owner` (its worker also caught only `Exception` — could never observe the CancelledError it asserts).
- **Environment leakage (36 failures):** repo-root `.env` is shared with the Docker deployment and pins `DEER_FLOW_CONFIG_PATH` / `DEER_FLOW_EXTENSIONS_CONFIG_PATH` / `DEER_FLOW_REPO_ROOT` / `DEER_FLOW_HOME` to in-container `/app/...` paths plus `DEER_FLOW_ENV=production`. `load_dotenv()` injected these into host pytest runs: gateway config load raised FileNotFoundError → every TestClient request 503'd (amd, channels, internal_auth, config_freshness, langgraph_auth, client_e2e), and the auth-disabled safety veto 401'd all `DEER_FLOW_AUTH_DISABLED` tests. Fix: `tests/conftest.py` pre-sets host-correct values before any `load_dotenv()` (dotenv never overrides existing vars); production `.env` untouched.
- **Phase C6 drift:** `test_cancel_run_idempotent` stubbed only `app.state.run_manager`; the C6-migrated endpoints resolve `run_service` → 503. Test app now provides `RunServiceImpl(mgr)` like the gateway lifespan.
- **Watchdog drift:** `test_healthcheck_daemon` run-cycle tests mocked 11 probes; the daemon runs 12 since P12 (tunnel). Probe list + counts updated.
- **Phase C6 drift:** `test_service_layer.py` + `test_wait_disconnect_handling.py` used deprecated `asyncio.get_event_loop().run_until_complete()` — breaks after any earlier `asyncio.run()` in the main thread (Python 3.12). Migrated to `asyncio.run()`.
- **Real C5 bug in `worker._service_set_status`:** outside the gateway, the lazy container factory builds a *fresh* `RunManager`; status updates routed there are silently dropped (run not found, nothing raises, fallback never fires). Now verifies the container RunService is backed by the worker's own RunManager before routing; falls back otherwise. Fixes `test_run_worker_rollback` / `test_run_worker_recursion_limit`.
- `test_stream_diagnostics` fixture popped `deerflow.runtime` / `app.gateway.services` from `sys.modules` at teardown, splitting sentinel identity (`END_SENTINEL` `is`-checks failed downstream) and orphaning package attributes (`deerflow.runtime` lost `runs` for monkeypatch walks). Teardown now restores original module objects and re-binds parent/child module attributes in both directions.
- `test_tracing_factory` monkeypatched `get_tracing_config` with a config *instance* instead of a callable (`'Cfg' object is not callable`). Wrapped in lambdas.
- `test_cancel_store_only_run_returns_409` encoded the pre-v8.0 contract; Phase 6 deliberately made store-only cancel persist `interrupted` through the RunStore and return 202. Test updated to assert the current semantics (renamed `..._persists_interrupted`).
- `test_thread_run_messages_pagination` + `test_cancel_run_idempotent` stubbed only `run_manager`; C6 endpoints resolve `run_service` → 503. Test apps now provide `RunServiceImpl(mgr)`.
- `docker/dev-entrypoint.sh` added `--reload-exclude=/app/backend/tests` without pre-creating the directory; mkdir added.
- `test_runtime_model_env_resolution` relies on sibling-of-config resolution; ambient `DEER_FLOW_HOME` redirected it to the deployment's root-owned file. Autouse fixture now clears the two env vars.
- Remaining known-red: `test_client_live.py` (4) — live smoke tests against this box; blocked by root-owned `.deer-flow/users/*` dirs created by the container (PermissionError) plus live-LLM nondeterminism (GraphRecursionError). Needs `chown` (interactive sudo) — operational, not code.

---

## v7.5 — live audit hardening + Ollama/LiteLLM free-model gateway

**Session pattern:** full-stack live audit (act-as-user via Playwright) → every blocker turned into a production-grade fix with a regression test.

### Audit fixes (all with tests)

- **Preview proxy 500 (P0):** the Batch-3.1 per-method route wrappers in `app/gateway/routers/sandbox.py` were sync `def` returning un-awaited coroutines — every `/api/sandbox/preview|lpreview|absproxy` request 500'd ("'coroutine' object is not iterable"), breaking the Browser tab. Made async; pinned by `test_preview_route_handlers_async.py`.
- **llama-bridge drift:** pm2's saved dump pointed at a deleted script path; the "online" process was orphaned stale code on the wrong port. Re-registered from `ecosystem.config.js`; watchdog `fix_llama_bridge()` now heals both crash and drift.
- **Watchdog gaps:** P9 had no auto-fix and warned every 30s unread for days. Added P9 + P10 auto-fixes and streak-deduped the no-auto-fix warning.
- **Strict chat-template 400:** middlewares inject SystemMessages mid-conversation; llama.cpp templates reject system at position > 0. New `SystemMessageCoalescingMiddleware` (innermost) folds them into the single leading system message.
- **Context overflow classified:** llama.cpp `exceed_context_size_error` now maps to a non-retriable `context_overflow` reason with an actionable message.
- **Silent verify skip:** auto-verify-on-present_files no-opped silently on non-AIO sandboxes — a corrupted deliverable shipped as "verified clean". Skip is now logged as "deliverable NOT verified".
- **Clobbered-HTML backstop:** deterministic review flags `.html` deliverables that don't start like HTML (`_scan_malformed_html_risks`) — catches the chunked-write overwrite failure observed live (maree.html written 3×, each write clobbering the last).
- **Orphan tool messages:** frontend grouping now renders tool results whose parent AI message was stripped instead of console.error + drop.
- **CSP blocked the Browser tab (nginx):** the app-shell CSP had no `frame-src`, so the blob-URL preview iframe (which inherits the parent page's CSP) was refused, and Google Fonts pulled by generated HTML were blocked. `docker/nginx/nginx.conf` `$csp_policy` map now allows `frame-src 'self' blob: http://localhost:*` plus fonts.googleapis.com/fonts.gstatic.com in the app-shell policy only — the strict `/api/` policy (`default-src 'none'`) is untouched. Note: the nginx container copies the mounted conf from a template at start, so `docker restart deer-flow-nginx` (not `nginx -s reload`) is required to apply edits.

### Ollama + LiteLLM free-model gateway

- `docker/litellm/config.yaml` + `scripts/pm2-litellm.sh` + pm2 app `nova-litellm` (LiteLLM 1.91.0 in a dedicated venv at `~/.nova-litellm`, bound to the docker bridge IP only).
- Four verified-free Ollama cloud models registered via the runtime models API: MiniMax M3, Nemotron 3 Super, Qwen3 Coder 480B, GPT-OSS 120B (`*-free`), reachable from the gateway at `host.docker.internal:4000/v1`.
- Watchdog P10_litellm probe + auto-fix; 10/10 probes green.
- Settings → Models: new "Add Ollama model (via LiteLLM)" preset (en/zh locales).

### Ecosystem: OpenCode + Dify on the same LiteLLM gateway

- **OpenCode**: `nova-litellm` provider in the global config (`~/.config/opencode/opencode.jsonc`) with all four free models and real limits pulled from `ollama show` (MiniMax M3 524K / Nemotron 262K / Qwen3-Coder 262K / GPT-OSS 131K context, 64K output); nova's `.opencode/opencode.json` pins `qwen3-coder-480b-free` as project default. Verified live via `opencode run`.
- **Dify** (fork `Jahanzaib211/dify` at `~/Desktop/dify`): full stack under PM2 as `nova-dify` (foreground compose, same pattern as `deerflow`), UI on `127.0.0.1:8088` only — upstream's 0.0.0.0 plugin-debug mapping replaced via `ports: !override`. Containers reach LiteLLM through `host.docker.internal:host-gateway`. Provider + 4 models configured as a real user via Playwright (context sizes set explicitly — the OpenAI-compatible plugin defaults to 4096); E2E chat verified ("DIFY-LITELLM-OK" on minimax-m3-free). Session lifetimes raised for the localhost-only install (access 7d / refresh 365d). Fork-side files committed to `Jahanzaib211/dify@main`.
- **Watchdog P11_dify**: probes `/console/api/setup` for `step=finished` (api-up-but-uninitialized reads as unhealthy); auto-fix heals the `nova-dify` pm2 app with re-register-on-drift. 11/11 probes green live; regression tests added (probe validator, both fix paths, dispatch streak).

### Attribution

- `NOVA_VS_DEERFLOW.md` — verified upstream-vs-Nova attribution map (fork base deer-flow v2.0.0-rc1, reproducible diff commands); README "What Nova adds" rewritten to match.
- Attribution numbers recomputed pre-commit: 338 files, +35,738/−1,278 vs v2.0.0-rc1 (152 new files ~28.7k lines; 37 new backend test files).
- README hero: `docs/images/nova-workspace.png` — real capture of a MiniMax M3 (free) session building a tip calculator, previewed live in the Agent's Computer Browser tab (zero console errors at capture).

---

## v8.8 — Phase C8: execution runtime kernel

**Session pattern:** production forensic → execution hardening → test coverage.

### C8.1 — Execution Kernel completeness

- **PTYManager** (`execution/pty_manager.py`): portable PTY allocation via `os.openpty()`, window-size control via `fcntl.ioctl TIOCSWINSZ`, file-descriptor lifecycle. `TerminalSize` dataclass with `to_winsize()` / `from_winsize()`.
- **SessionRegistry** (`execution/session_registry.py`): `ShellSession` dataclass + `SessionRegistry` for full interactive session lifecycle — PTY fds, pid, cwd, env, terminal size, heartbeat, I/O metrics. `SessionState` enum: ALLOCATED, STARTING, RUNNING, WAITING, STOPPING, STOPPED, ZOMBIE, ERROR.
- **InteractiveShellAdapter** (`execution/adapters/interactive_shell.py`): per-session PTY spawning with I/O locks, heartbeat thread, SIGWINCH propagation on resize. Uses `subprocess.Popen` with `start_new_session=True`.
- **Ownership maps** in `Supervisor`: bidirectional `execution_id ↔ run_id ↔ session_id ↔ pid` maps; methods: `register_execution()`, `unregister_execution()`, `get_execution_id()`, `get_run_id()`, `get_session_id()`.
- **ProcessHeartbeat** in `Supervisor`: `ProcessHeartbeat` dataclass with `pid`, `started_at`, `last_heartbeat`, `session_id`; `_heartbeats` dict; `update_heartbeat()`, `get_heartbeat()`, `scan_zombies()`.
- **Heartbeat thread** in `kernel.execute_sync()`: 5s interval, stops on process exit/cancel/timeout; emits `ProcessHeartbeat` domain events.

### C8.2 — Two-phase cancellation

- **`_two_phase_cancel()`** in `kernel.py`: SIGINT → SIGTERM → SIGKILL escalation chain with configurable grace periods.
- **`Supervisor.cancel()`**: recursive child cancellation first (deepest first), then target; checks `_popen` then `_spawned`; `was_cancelled()` for status tracking.
- **`_schedule_async_termination()`**: `loop.call_soon()` fires termination asynchronously so cancel returns immediately (Phase 1) while SIGKILL fires later (Phase 2).

### C8.3 — ExecutionStatus state machine

- Expanded `ExecutionStatus` enum with 11 states: PENDING, ALLOCATED, PREPARING, RUNNING, WAITING_INPUT, STREAMING, CANCELLING, STOPPING, SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, STOPPED, DENIED, ZOMBIE_DETECTED, REAPED.
- `is_terminal`, `is_active`, `is_cancellable` properties on every status.
- `ExecutionRequest` gains `parent_execution_id` (for child tree tracking) and `session_id` fields.
- `ResourceLimits` gains `max_depth`, `max_children`, `max_recursion`, `heartbeat_interval`.

### C8.4 — Execution budget (scheduler)

- `DepthTracker`: per-run depth counter incremented on admit, decremented on release.
- `ChildTracker`: per-parent child count incremented on admit, decremented on release; `get_child_count()`.
- `Scheduler.admit()` checks depth/child budget before admission; `DENIED` result when saturated.

### C8.5 — Tests

- `tests/test_execution_pty.py`: 23 tests covering PTYManager, TerminalSize, SessionRegistry, heartbeat, two-phase cancellation, ExecutionStatus state machine.
- `tests/test_execution_guardrails.py`: guardrail updated to whitelist `interactive_shell.py` as sanctioned Popen site.
- All 6,430 backend tests pass; 565 frontend tests pass; cross-ref check clean.

### Known debt

- `InteractiveShellAdapter` has a preexec conflict: `start_new_session=True` already calls `setsid()`; redundant `preexec_fn=os.setsid` causes `SubprocessError` in some environments. Tests skipped pending fix.
- `SessionRegistry.snapshot()` had a deadlock (calling `list_active()` while holding lock); fixed by inlining the active-count logic.

---

## Operational IDs (intentionally preserved)

- docker-compose project name: `deer-flow-dev`
- Container names: `deer-flow-{nginx,frontend,gateway}`
- pm2 process: `deerflow`
- Python package: `deerflow.*`
- Env vars: `DEER_FLOW_*`
- Data dir: `backend/.deer-flow/`

These are stable; only docs / user-facing strings change.

---

## Hard rules (carried forward from FORK_V* + AI-First Engineering)

1. **Additive / reversible only.** Every change is one revert away.
2. **Local-sandbox path byte-identical.** `is_local_sandbox` branch (sandbox/tools.py:1204) preserved.
3. **Batch backend edits.** uvicorn `--reload` recreates active sandboxes.
4. **Anti-slop.** Reuse existing primitives; no parallel implementations.
5. **Non-fatal.** Every new code path is wrapped so a failure can never break a run.
6. **Harness boundary.** `deerflow.*` never imports `app.*` (enforced).
7. **Replay E2E is the contract.** `tests/fixtures/replay/write_read_file.ultra.{json,events.json}` is the keystone test.
