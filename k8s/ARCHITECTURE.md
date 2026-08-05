# Nova Architecture — Production-Readiness Reference

Evidence-based, not aspirational. Every claim here traces to a specific
file, line, or command output gathered by direct code reading and live
verification against the running staging deployment. This document is
the source of truth for the production-hardening work in `k8s/README.md`'s
later phases — updated as each phase lands, not a point-in-time snapshot.

**Scale context, stated plainly**: this entire stack — production Compose
deployment and the K8s staging deployment alike — runs on one shared box
(Ryzen 5 3600, 30GB RAM), also hosting unrelated tenants (`gvm`,
`fox-hosting`). The K8s cluster is single-node. Claims in this document
about what's "possible" or "designed for" multi-node/high-concurrency
operation describe architecture, not something demonstrated at that scale
on this hardware.

## 1. Every Service

| Service | Runtime today | Notes |
|---|---|---|
| **Gateway** | Docker container (prod: `docker/docker-compose.yaml`), also a K8s Deployment (staging) | FastAPI + embedded LangGraph runtime, port 8001. See §4 for the state architecture that currently caps it at 1 replica. |
| **Frontend** | Docker container / K8s Deployment | Next.js, prod-baked (`next start`), stateless, port 3000. |
| **nginx** | Docker container / K8s Deployment | Reverse proxy, port 2026. Rate-limit zones, CSP/security headers, SSE buffering, regex `/api/threads` routing — not a clean 1:1 Ingress-annotation translation (see §9). |
| **Provisioner** | Docker container / K8s Deployment (or a K8s Pod itself, staging) | `docker/provisioner/app.py` — creates/destroys per-thread sandbox Pods+Services via the K8s API. The one genuinely K8s-native piece of Nova that predates this session's work. |
| **Sandbox execution** | Per-thread Docker container (DooD) *or* per-thread K8s Pod (provisioner-managed) | Job-like, ephemeral — not a long-running Deployment. Verified live: a real chat run creates a real sandbox Pod+Service, executes a command, tears down after a 600s idle timeout. |
| **searxng** | Docker container, dev only (`docker-compose-dev.yaml:280`) | Self-hosted search backing the privacy-search (iGIN0) feature. |
| **nova-litellm** | **Bare PM2 process** (`172.17.0.1:4000`) | **Not containerized, not K8s-aware at all.** `scripts/healthcheck-daemon.py:22` probes `172.17.0.1:4000/v1/models`; `fix_litellm()` restarts it via `pm2 restart nova-litellm` or `pm2 start ... --only nova-litellm` from `ecosystem.config.js`. A real, currently-unmanaged-by-either-Compose-or-K8s dependency. |
| **IM channel workers** (Slack/Telegram/Discord/Feishu/DingTalk/WeChat/WeCom) | Run **inside the Gateway process** (`app/channels/service.py`) | Not a separate deployable unit today. See §5 — this is a real blocker to Gateway horizontal scaling, not just an implementation detail. |
| **MCP servers** | Subprocesses or remote HTTP/SSE launched by the Gateway process itself | Configured per-server in `extensions_config.json`; not independently deployed. |
| **Browser/verify workers** | Tooling inside each sandbox Pod/container (`browser_check`, `dev_verify`) | Not a standalone service. |
| **PostgreSQL** | **Not deployed anywhere.** Config-option only (`database.backend: postgres`) | Real, working code exists — `AsyncPostgresSaver`, connection pooling (`checkpointer/async_provider.py:73,111-118`) — documented as the "production multi-node deployment" backend, currently unused. Live `config.yaml` has `database.backend: sqlite`. |
| **Redis** | **Not deployed, not implemented.** | `stream_bridge_config.py:15`: *"'redis' uses Redis Streams (planned for Phase 2, not yet implemented)"*. `runtime/stream_bridge/async_provider.py:52-53` raises `NotImplementedError` for it today. No `redis`/`aioredis` Python dependency exists in `backend/pyproject.toml`. See §4/§10 — this is the actual scaling blocker being fixed. |
| **Qdrant** | **Does not exist in this codebase.** Zero references anywhere. | |
| **Ollama / LiteLLM (as model providers)** | Not deployed by Nova — Ollama is a model provider option a user points Nova at (`scripts/wizard/providers.py:220-255`); "LiteLLM" as a term only appears as the `nova-litellm` PM2 proxy above, unrelated to the LiteLLM SDK/framework. | |

## 2. Every Dependency, Network Connection, Volume (summary — full detail in §3/§7/§8)

- **Nginx → Gateway/Frontend/Provisioner**: Compose bridge-network DNS (container names) in production; K8s Service DNS in staging. See §9 for the exact DNS-resolution bug this session found and fixed (nginx's dynamic `resolver` needs full Service FQDNs, not short names).
- **Gateway → Provisioner**: HTTP, `sandbox.provisioner_url` config field — the entire DooD→K8s-sandbox switch is this one field (`aio_sandbox_provider.py:186-189`).
- **Gateway → nova-litellm**: HTTP, `172.17.0.1:4000` (Docker bridge gateway IP reaching the host's PM2 process) — a cross-runtime dependency (container → host process) that would need re-plumbing if nova-litellm were ever containerized.
- **Gateway → LLM/search providers**: outbound HTTPS to each configured provider's API.
- **IM channel workers → external platforms**: persistent WebSocket (Slack Socket Mode, Discord gateway) or long-polling (Telegram `getUpdates`) connections, initiated from inside the Gateway process.
- **Provisioner → K8s API**: in-cluster ServiceAccount token (staging) or mounted kubeconfig (Compose `docker-compose.yaml:183`), scoped RBAC (`k8s/charts/nova/templates/role-provisioner.yaml` + 2 ClusterRoles for namespace-read and metrics-read).

## 3. Every Secret / Environment Variable

Full detail gathered by exhaustive `os.environ.get`/`os.getenv` search across `backend/app/` and `backend/packages/harness/deerflow/`.

### (a) Third-party API keys

`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LLM_API_KEY`, `OPENAI_API_BASE`/`LLM_BASE_URL`, `GITHUB_TOKEN`, `BRAVE_SEARCH_API_KEY`, `SERPER_API_KEY`, `INFOQUEST_API_KEY`, `JINA_API_KEY`, plus config.yaml-interpolated (`$VAR`) keys for Tavily/Firecrawl/Volcengine/Gemini/Deepseek/Novita/Minimax/Stepfun/VLLM/Fireworks.

### (b) Session / auth secrets

`AUTH_JWT_SECRET` (auto-generated + persisted to `{base_dir}/.jwt_secret` mode 0600 if unset — a durability dependency on `DEER_FLOW_HOME`/PVC persistence), `DEER_FLOW_INTERNAL_AUTH_TOKEN` (required in production, process refuses to start without it), `NOVA_OPS_TOKEN`, `DEER_FLOW_AUTH_DISABLED`, `NOVA_BYOK_SECRET` (Fernet key encrypting user API keys at rest), `NOVA_BYOK_ENABLED`, `AUTH_TRUSTED_PROXIES`.

### (c) Infrastructure connection strings

`DATABASE_URL` (Postgres DSN via `$DATABASE_URL` config.yaml interpolation), `DEER_FLOW_DATABASE_URL` (**separate** env var used only by Alembic migrations — a documented drift risk between the app's own DB-URL resolution and the migration tool's), `DEER_FLOW_SQLITE_DIR`. No Redis connection var exists yet (added in Phase 2).

### (d)/(e) Feature flags, paths, misc

`NOVA_CREDITS_ENFORCED`, `GATEWAY_CORS_ORIGINS`, `GATEWAY_ENABLE_DOCS`, `DEER_FLOW_ENV`/`ENVIRONMENT`, the full `DEERFLOW_IGINO_*` privacy-search config block, `DEER_FLOW_HOME`/`DEER_FLOW_PROJECT_ROOT`/`DEER_FLOW_CONFIG_PATH`/`DEER_FLOW_EXTENSIONS_CONFIG_PATH` path overrides, tracing env names (`LANGSMITH_*`, `LANGFUSE_*`).

### Secrets gap — closed in Phase 6

`k8s/scripts/bootstrap-secrets.sh` originally populated exactly 10 keys in `nova-secrets`:

```
BETTER_AUTH_SECRET, DEER_FLOW_INTERNAL_AUTH_TOKEN, NOVA_OPS_TOKEN, SEARXNG_SECRET_KEY   (freshly generated)
FIREWORKS_API_KEY, MINIMAX_API_KEY, TAVILY_API_KEY, JINA_API_KEY, SERPER_API_KEY, INFOQUEST_API_KEY   (from .env)
```

Compose's `env_file: ../.env` pattern (`docker-compose.yaml:154-155,192-193`) implicitly grants the container access to **every** variable in root `.env` — the K8s script only covered 10, leaving `DATABASE_URL`, Stripe, BYOK, `GITHUB_TOKEN`, tracing keys, most LLM/search providers, and every IM channel credential silently unbootstrapped. Phase 6 expanded the script to explicitly enumerate every secret-shaped env var the codebase actually reads (found via `grep -rn os.environ` across `app/`/`packages/harness/` plus every `$VAR` reference in `config.example.yaml`) — 48 keys now, not a blind full-`.env` passthrough (that would risk local dev path values like `DEER_FLOW_HOME` silently overriding `configmap-env.yaml`'s correct in-container paths, since `secretRef` is listed after `configMapRef` in every Deployment's `envFrom`). It also now generates `AUTH_JWT_SECRET` itself rather than relying on `auth/config.py`'s file-persisted fallback, which has a real check-then-act race across replicas on a fresh PVC — see §9 bug #12.

### nova-ops (separate repo) secrets

`GATEWAY_URL`, `NOVA_OPS_TOKEN` (must match Nova's own value — a shared secret across two repos), `OPS_PASSCODE`, `OPS_SESSION_SECRET`.

## 4. State Architecture — the actual scaling blocker, with exact code references

**`RunManager` (`backend/packages/harness/deerflow/runtime/runs/manager.py`)** holds `self._runs: dict[str, RunRecord]` and `self._runs_by_thread` (line 136-141) — process-local, `asyncio.Lock`-guarded. Two fields on `RunRecord` are the actual, specific blockers:

1. **`task: asyncio.Task | None`** — the live coroutine driving the run (`app/gateway/services.py:511`: `record.task = task` right after `asyncio.create_task(run_agent(...))`). Bound to one process's event loop; no other process can ever reference it.
2. **`abort_event: asyncio.Event`** — the cooperative cancel signal `run_agent()`'s streaming loop polls (`worker.py:345/366`). Meaningless cross-process — a different replica cannot `.set()` another process's `Event`.

**There already is a clean split** between this live-coordination state and durable history: `RunStore` (`runs/store/base.py`, an `abc.ABC`) is already SQL-backed and already cross-replica-safe — `RunManager.get()` checks `self._runs` first, falls back to the store, and the docstring is explicit: *"In-memory records win for the same run_id so task, abort, and stream-control state stays attached to active local runs."* The refactor's job (Phase 2) is narrowly scoped: make the coordination half cross-replica-aware, not touch the storage half.

**`StreamBridge`** (`runtime/stream_bridge/base.py`) is a clean `abc.ABC` — `publish`/`publish_end`/`subscribe`/`has_run`/`cleanup`/`close`. The default `MemoryStreamBridge`'s `subscribe()` is already an offset/cursor-based replay loop (`StreamEvent.id` format `"{ts_ms}-{seq}"`, coincidentally Redis-Stream-ID-shaped already) — confirmed via full code reading that a `RedisStreamBridge` is "write one new class implementing the interface," not a redesign.

**`create_or_reject()`'s TOCTOU-safe inflight check** (`manager.py:732-844`) uses the same process-local `asyncio.Lock` — with 2+ replicas, two concurrent run-creation requests hitting different replicas can both pass the "no active run for this thread" check simultaneously, reintroducing the exact race the lock was built to eliminate.

**Rate limiter** (`app/gateway/auth_rate_limit_middleware.py:13`) — already self-documented in-code as single-node-only: *"for multi-node deployments back this with Redis."*

**IM channel workers — the confirmed duplicate-delivery risk.** No leader election or single-active-instance concept exists anywhere in `app/channels/`. `ChannelService.start()` unconditionally starts every enabled channel (`service.py:145-155,310-342`). Slack opens a persistent Socket Mode WebSocket, Telegram starts a dedicated long-polling thread (`getUpdates`), Discord opens its own gateway connection — each **per Gateway process**. Running 2 Gateway replicas with the same channel credentials today would start 2x duplicate connections per platform, with the existing inbound-dedup cache (`manager.py:66-71`) being itself process-local and therefore blind to a redelivery landing on the *other* replica. **This is a real, currently-latent bug**, not a hypothetical — confirmed by reading the actual connection-lifecycle code, not inferred.

## 5. Self-Healing (current division of responsibility)

- **Health probes**: Docker Compose `healthcheck:` blocks per service (Compose semantics); K8s chart has real `startupProbe`/`readinessProbe`/`livenessProbe` per Deployment (written from scratch this session — a different mechanism from Docker healthchecks, not a straight carry-over).
- **Restart**: Compose `restart: unless-stopped`; K8s Deployments get this for free from the controller.
- **Watchdog**: `scripts/healthcheck-daemon.py` (PM2-managed) actively probes 10 things (P1-P10) including `nova-litellm` reachability, with real auto-fix logic (`fix_litellm()` restarts via PM2). This is host-level, not cluster-aware, and has zero interaction with the K8s deployment — a real integration gap if nova-litellm is ever containerized.
- **PM2**: central to production ops — nova-ops, cloudflared tunnels, and the healthcheck daemon itself all run under it (`ecosystem.config.js`).

## 6. Persistent Storage

| Component | Today | K8s treatment |
|---|---|---|
| SQLite (default) | Bind-mounted file, `${DEER_FLOW_HOME}:/app/backend/.deer-flow`, WAL mode | Static hostPath PV (`pvc-deerflow-data.yaml`) — deliberately static (not dynamic `local-path`) so sandbox Pods and Gateway see identical files at a predictable path |
| PostgreSQL | Not deployed | Would need its own StatefulSet + PVC if self-hosted — not built (Postgres isn't in use yet) |
| Redis | Not deployed | New in Phase 2 — single-replica Deployment + PVC (AOF persistence) |
| User uploads/outputs/memory | Same `.deer-flow` tree | Same PVC as SQLite |

### Disaster recovery (Phase 7)

- **Backup**: `cronjob-backup.yaml`, hourly, via `files/backup.py` — SQLite backed up through sqlite3's own online `.backup()` API (WAL-safe against a live database with concurrent writers, unlike a raw file copy which can catch a mid-write state), plus `extensions_config.json`, tarred into a hostPath directory with a rolling retention count (default 72 = 3 days of hourly backups).
- **Honest limitation**: the backup destination is a hostPath on the *same physical node* as the cluster it backs up (this is a single-node box — there is no second node or object storage configured). This protects against bad deploys, data corruption, and operator error; it does **not** protect against a whole-node hardware failure, which would take the live data and every backup with it together.
- **RPO**: bounded by the backup interval — 1 hour.
- **RTO**: measured, not guessed, via a real restore drill — a fresh `helm install` of this chart into a scratch `nova-dr-drill` namespace, pointed at a restored copy of a real production backup's SQLite file, from "namespace doesn't exist" to "verified working login against the restored users" took **~8 minutes** end-to-end. That drill is what surfaced bug #14 in §9 below — a real chart bug, not a hypothetical one.
| Skills | Read-mostly, repo-tracked | `hostPath` (not PVC) — deliberately, it's authoring content shared with the host, not runtime state |
| Extensions config (MCP/skills toggles) | Bind-mounted, read-write | PVC + initContainer seeded from a ConfigMap default (read-only ConfigMap mount wouldn't allow the runtime writes the Gateway API needs to make) |

## 7. Networking

Production: Docker bridge network, nginx single entrypoint (`:2026`), Cloudflare Tunnel fronting it — no Kubernetes involved. Staging: K8s Service DNS, NodePort (firewalled to loopback/LAN via `raw`-table `iptables`/`ip6tables` rules — see §9's nginx-DNS bug for why a plain `INPUT`-chain rule would have silently done nothing).

**Ingress compatibility: deliberately not adopted.** nginx's rate-limit zones, CSP/security headers, SSE buffering, and regex-heavy `/api/threads` routing aren't a clean 1:1 Ingress-annotation translation — nginx stays a real Deployment+Service, config ported near-verbatim (only Service DNS names changed).

## 8. CI/CD — current state

14 workflows in `.github/workflows/`. Quality gates are solid: backend/frontend unit tests, a purpose-built blocking-IO regression gate, Playwright e2e, lint+typecheck, and CodeQL (weekly + every PR, though SARIF upload is disabled — no GitHub Advanced Security on this private repo, so alerts don't surface anywhere despite the scan running).

`container.yaml` (the only image-publishing workflow, triggered on `v*` tags): build → push to GHCR → `actions/attest-build-provenance` (GitHub-native build provenance). **Confirmed absent**: vulnerability scanning (no Trivy/Grype/Snyk), SBOM generation (no syft/cyclonedx), image signing (no cosign invocation — the provenance attestation is Sigstore-backed under the hood but there's no explicit signature). Fixed in Phase 4.

## 9. Real bugs found and fixed building the K8s staging deployment (this session)

Kept here as living architecture knowledge, not just a changelog entry — each one reveals something true about the system that the next engineer needs to know:

1. **nginx's dynamic `resolver`-based DNS doesn't apply `/etc/resolv.conf` search domains** — needs full Service FQDNs, not short names, when using `set $x upstream; proxy_pass http://$x;` for per-request re-resolution.
2. **nginx's 60s default `proxy_read_timeout`** silently killed any `/api/threads/*runs/wait` or `/api/runs/*` request running longer than that — fixed in *production* Compose nginx.conf too, not just staging.
3. **`SKILLS_HOST_PATH` on the provisioner is a real node-filesystem path**, not a path inside the provisioner's own container — it's embedded verbatim into the *sandbox* Pod's hostPath volume spec.
4. **k3s enforces `NetworkPolicy` by default** — embeds kube-router's controller inside the k3s binary itself (no separate pod), active unless `--disable-network-policy` was passed at install (it wasn't). A missing allow-rule for sandbox Pods caused connection-refused on every access path (NodePort, ClusterIP, direct Pod IP) while host-originated kubelet probes worked fine — looked exactly like a hairpin-NAT bug until traced to the real cause.
5. **`fsGroup` does not cover static hostPath-backed PVs** — only dynamic `local-path` PVCs get ownership management; a one-shot root `chown` initContainer is the correct fix, confirmed by evidence (the error persisted after adding `fsGroup`) rather than assumed from docs.
6. **`DatabaseConfig._resolved_sqlite_dir` resolves relative to raw process CWD**, not `DEER_FLOW_HOME` — SQLite was never actually persisted on the PVC before this was caught (two replicas of the same "user" silently had two disconnected databases). Fixed via the `DEER_FLOW_SQLITE_DIR` override env var, which the property already supported.
7. **redis-py's default `socket_timeout` races against `XREAD BLOCK` / `pubsub.listen()`** — both are legitimately long-lived blocking server-side reads; the client-side socket timeout fires first and surfaces as a spurious `redis.exceptions.TimeoutError` under real load. Fixed via explicit `socket_timeout=None` on every Redis client this codebase constructs.
8. **`kubernetes` Python client v36.0.3's `read_namespaced_pod_log` sometimes returns the literal `repr()` of a `bytes` object** (`"b'...\\n...'"`) instead of decoded text, for non-JSON (`text/plain`) log responses — a real upstream library bug, reproduced directly against the live cluster. Worked around via `ast.literal_eval` in the provisioner's `_normalize_pod_log_text()`.
9. **`init_engine_from_config` passed the raw, unresolved `DatabaseConfig.sqlite_dir` field to `os.makedirs()`**, not the `_resolved_sqlite_dir` property that `sqlite_path`/`app_sqlalchemy_url` actually use — silently created an unused directory under CWD whenever CWD happened to be writable (every environment before k8s), and crashed outright once Phase 5 made the root filesystem read-only. Fixed by passing the resolved property everywhere; regression-tested in `test_persistence_scaffold.py`.
10. **`pnpm start`'s corepack writes its package-manager cache to `$HOME/.cache`** — surfaced only once `readOnlyRootFilesystem` made `/home/node` non-writable. Same fix pattern as gateway's `UV_CACHE_DIR`/`HOME`: point `HOME` at the pod's `/tmp` emptyDir.
11. **nginx's temp-file directories (`client_temp`, `proxy_temp`, etc.) must pre-exist under `/var/cache/nginx`** — the stock `nginx:alpine` image bakes them in at build time, but mounting an `emptyDir` over `/var/cache/nginx` for `readOnlyRootFilesystem` starts empty, and nginx creates only the *hashed subdirectories* under a temp path on demand, never the top-level path itself. Fixed with a root initContainer that `mkdir -p`s them into the mounted emptyDir before the (already-root) nginx master process starts.
12. **`auth/config.py`'s JWT secret has a check-then-act race across replicas**: when `AUTH_JWT_SECRET` isn't set, it falls back to reading (or, on first boot, generating and persisting) `{base_dir}/.jwt_secret` on the shared PVC — with no file locking. Two gateway replicas starting simultaneously against a *fresh* PVC (first install, or a Phase 7 DR restore into a scratch namespace) can each see "file doesn't exist yet," generate a *different* secret, and both persist to the same file — whichever write lands last wins the file, but the losing replica already cached the other value in memory, so tokens it issues/validates stop matching everything else from that point on. Fixed by having `bootstrap-secrets.sh` generate `AUTH_JWT_SECRET` itself (same pattern as `BETTER_AUTH_SECRET` etc.), so every replica sees the same value before any of them start — the race path never executes in K8s.
13. **Pod Security Standards' "HostPath Volumes" control is forbidden under *both* `baseline` and `restricted`**, not just `restricted` — gateway and channels' `skillsHostPath` mount means the namespace-wide `enforce` level can't go above `privileged` without rejecting every gateway/channels pod outright, confirmed against the upstream PSS spec before labeling anything. `warn`/`audit` at `restricted` give real, non-blocking visibility instead (see `templates/namespace.yaml`).
14. **`ClusterRole`/`ClusterRoleBinding` names were hardcoded literals** (`nova-provisioner-metrics-reader`, `nova-staging-namespace-reader`), not templated by `.Values.namespace` — cluster-scoped resources collide across releases regardless of namespace, so installing a second release of this chart anywhere on the same cluster (exactly what the Phase 7 DR drill needs to do) failed outright with a Helm ownership-conflict error. `pvc-deerflow-data.yaml`'s `PersistentVolume` name had the same issue, fixed by adding a `deerflowDataPVName` value that *defaults* to the original literal name — `PVC.spec.volumeName` is immutable once bound, so the live release's default had to stay byte-identical while still being overridable for a second release.
15. **A real live incident bootstrapping Flux, caused by config drift, not a Flux bug.** `k8s/flux/staging/helmrelease.yaml`'s `values:` block was a hand-copied snapshot of `values-staging.yaml` from Phase 3, before Phases 5 and 7 added `resources.gateway.maxReplicas`/`resources.frontend.maxReplicas`, `deerflowDataHostPath`, and `backup.hostPath` — nobody had gone back to update the copy. When the `GitRepository`/`Kustomization` reconcile problems below finally cleared and the `HelmRelease` reconciled for the first time, it applied that stale config: `deerflowDataHostPath` and `backup.hostPath` both resolved to their chart defaults (`""`), which is an **invalid, empty `hostPath.path`** — the live `PersistentVolume` and the backup `CronJob` both failed `server-side apply` outright and were left deleted (the underlying host data at `/var/lib/nova-staging/deerflow-data` survived, since `persistentVolumeReclaimPolicy: Retain` only ever governs the PV *object*, never the real files — recovery was a straight re-`helm install`, zero data loss, verified by querying the restored SQLite database directly). `nova-secrets` also went missing during the same incident; no chart template has ever defined a `Secret` named `nova-secrets` (confirmed via `git log -p` across this repo's full history), so the exact deletion mechanism was never conclusively isolated — `bootstrap-secrets.sh` re-creates it trivially, which is what actually resolved it, but this is flagged here as an open question, not a solved one. **Fixed at the root**: `k8s/kustomization.yaml`'s `configMapGenerator` now generates the `HelmRelease`'s values directly from the real `values-staging.yaml` file (Kustomize's load-restrictor blocks reaching that file from `k8s/flux/staging/`'s original location — even via a symlink, which resolves before the bounds check — so the Kustomization's `spec.path` moved to `./k8s` to cover both), making this specific class of drift structurally impossible rather than a discipline problem to remember next time.
16. **Reconciling this repo through Flux was itself slow and initially looked broken, but wasn't — it was a real, measured bandwidth constraint.** A direct `git clone --progress` timing test showed throughput on this box dropping from ~3 MiB/s to ~150 KiB/s mid-transfer on the same clone of this ~19MB/2000+-object repo (grown substantially this session). `GitRepository`'s default 60s clone timeout was marginal against that; bumped to 300s (`k8s/flux/staging/flux-system/gotk-sync.yaml`) once measured directly rather than guessed at. Earlier, weaker theories tried along the way (CPU contention; lock contention between two `GitRepository` objects pointing at the same URL, which motivated removing a redundant second `GitRepository` resource — a good simplification kept on its own merits, but not the actual fix) are corrected here for anyone reading the commit history in order.

## 10. What this document feeds into

- Phase 2 fixes §4's blockers for real (Redis stream bridge, cross-replica cancellation, distributed lock, channel extraction).
- Phase 4 fixes §8's CI/CD gaps.
- Phase 6 fixes §3's secrets gap and applies Pod Security Standards.
- Phase 7 builds real backup/restore for §6's storage.

See `k8s/README.md` for the operational runbook and `../.claude/plans/` history for the full phased plan this document supports.
