# Nova on K8s (staging)

A single-node k3s deployment of Nova (gateway ×2 + frontend ×2 + nginx +
sandbox provisioner + Redis + a dedicated IM-channels worker) running in
the `nova-staging` namespace, fully isolated from the live Docker Compose
production stack on the same box (separate runtime, network, storage, and
freshly-generated secrets). Sandbox execution runs as real K8s-native
Pods via the provisioner instead of Docker-out-of-Docker —
`AioSandboxProvider` switches automatically when `sandbox.provisioner_url`
is set in `config.yaml`, no code change needed.

Gateway and frontend are horizontally scaled with CPU-based
autoscaling and zero-downtime rolling updates (see Scaling below) — the
Redis-backed stream bridge, cross-replica cancellation, and distributed
locking that make a second gateway replica *safe* are documented in
`k8s/ARCHITECTURE.md` §4/§10. GitOps (Flux), CI image scanning/signing,
`readOnlyRootFilesystem`+`seccomp` on every workload, and an hourly
backup with a real, measured restore drill are all live too — see their
respective sections below.

Cutting real user traffic over to this deployment is a separate, later
decision — this is staging only.

## Prerequisites

- k3s installed without Traefik/servicelb (this box):

  ```bash
  curl -sfL https://get.k3s.io | sh -s - server \
    --disable traefik --disable servicelb \
    --write-kubeconfig-mode 644
  ```

  `~/.kube/config` is a copy of `/etc/rancher/k3s/k3s.yaml` (mode 644
  makes the copy sudo-free).
- Helm v3 (installed as a static binary, no sudo needed):
  `~/.local/bin/helm`
- k9s (optional, for visually poking around the cluster):
  `~/.local/bin/k9s` — just run `k9s` in a terminal, it's a TUI.

## First-time deploy

```bash
# 0. Same convention as the repo root: config.yaml is gitignored, never committed.
#    Seed it from the real one (or config.example.yaml) and add the one staging-specific key.
cp ../config.yaml k8s/charts/nova/files/config.yaml   # or: cp ../config.example.yaml ...
# then add to it:  sandbox: { provisioner_url: "http://provisioner.nova-staging.svc.cluster.local:8002" }

# 1. Build + import all 4 images into k3s's containerd (needs root for the import step)
k8s/scripts/build-images.sh staging

# 2. Generate fresh staging secrets + pull real API keys from ../.env
k8s/scripts/bootstrap-secrets.sh

# 3. Install
helm install nova k8s/charts/nova -n nova-staging --create-namespace \
  -f k8s/charts/nova/values-staging.yaml
```

## Redeploying after a code/config change

```bash
# Rebuild + reimport only the images that changed (build-images.sh rebuilds all 3 app images every run)
k8s/scripts/build-images.sh staging

helm upgrade nova k8s/charts/nova -n nova-staging -f k8s/charts/nova/values-staging.yaml
```

**Critical:** a `helm upgrade` that only changes a ConfigMap (e.g.
`config.yaml`, `extensions_config.default.json`) does **not** restart the pod
that mounts it. Two independent reasons, and the second is the one that bites:

1. The Deployment spec is byte-identical, so Helm generates no new pod
   template and therefore no rollout — `helm upgrade` reports success anyway.
2. These ConfigMaps are mounted with **`subPath`**, which kubelet never
   updates in place. The file inside the container is frozen at pod creation,
   so even a long-lived pod would never see the new content. (This is why the
   older wording here — "Kubernetes updates the file on disk in-place but the
   process keeps its old in-memory state" — was wrong: with `subPath` the
   on-disk file does not change either.)

`nginx` is now exempt: `deployment-nginx.yaml` carries a
`checksum/nginx-config` annotation computed from the ConfigMap template, so
changing `files/nginx.conf` rolls the pod automatically. Everything else still
needs an explicit restart:

```bash
kubectl rollout restart deployment/gateway -n nova-staging   # config.yaml, env vars
# nginx rolls itself via checksum/nginx-config — no manual restart needed
```

The same trap applies to **image rebuilds**: tags are static (`nova-gateway:staging`)
with `imagePullPolicy: IfNotPresent`, so rebuilding under the same tag also
produces an identical spec and no rollout. Always `kubectl rollout restart`
after `build-images.sh`.

This mirrors the same rule documented in the root `CLAUDE.md` for
`STARTUP_ONLY_FIELDS` in Compose — it's not new here, just easy to forget
because `helm upgrade` reports success either way.

## Verifying health

```bash
kubectl get pods -n nova-staging -o wide
kubectl top pods -n nova-staging          # confirm the box isn't being starved
curl http://127.0.0.1:30026/health         # through nginx -> gateway
kubectl describe resourcequota -n nova-staging
```

To confirm the sandbox path end-to-end (not just that pods start), trigger
a real chat run that uses the `bash` tool and watch for a sandbox Pod:

```bash
kubectl get pods,svc -n nova-staging -w
```

You should see `sandbox-<id>` and `sandbox-<id>-svc` appear, the pod reach
`1/1 Running` after ~10-15s (the AIO image's own internal boot time), and
disappear again after the provisioner's 600s idle timeout.

## Known gotchas hit building this (read before debugging a "it just hangs" issue)

The full, numbered list (14 and counting) lives in `k8s/ARCHITECTURE.md`
§9 with more context per entry — the ones below are the Phase 1 set plus
whichever later ones are most likely to actually bite you while working
day-to-day on this deployment. Reading both before debugging something
that "just hangs" or "just crashes" will save real time.

- **`readOnlyRootFilesystem: true` (every container, Phase 5) surfaced
  three previously-silent bugs the moment it landed** — worth knowing the
  shape of these before adding a new container to this chart: (1) code
  that writes to a relative path resolves against the container's CWD,
  which is now read-only — check for `os.makedirs`/`open(..., "w")`
  calls using anything other than an explicitly-mounted absolute path;
  (2) `$HOME`-based caches (`corepack`, `uv`, `pip`) need `HOME` pointed
  at a mounted `emptyDir` explicitly, they don't get a free pass; (3) any
  image with baked-in directories it expects to exist at a specific path
  (nginx's temp dirs) needs those recreated by an initContainer if an
  `emptyDir` is mounted over that path, since mounting shadows whatever
  was baked into the image there.
- **SQLite was never actually being persisted to the PVC** —
  `DatabaseConfig._resolved_sqlite_dir` resolves relative to raw process
  CWD, not `DEER_FLOW_HOME`. Two replicas of the "same" install can
  silently have two disconnected databases. Fixed via
  `DEER_FLOW_SQLITE_DIR` (`configmap-env.yaml`) — if you ever see a user
  registered on one replica come back "not found" on another, check this
  first, not the load balancer.
- **redis-py's default `socket_timeout` races against `XREAD BLOCK` /
  `pubsub.listen()`** — both are legitimately long blocking reads; the
  client-side timeout can fire first and surfaces as a spurious
  `redis.exceptions.TimeoutError` under real load, not a real Redis
  outage. Every Redis client this codebase constructs sets
  `socket_timeout=None` explicitly — don't add a new one without it.
- **Cluster-scoped resource names (`ClusterRole`, `ClusterRoleBinding`,
  `PersistentVolume`) must be templated by `.Values.namespace`, not
  hardcoded literals** — they collide across *any* two releases on the
  same cluster regardless of namespace, which only surfaces the moment
  you try to install a second release (e.g. a DR drill, or a second
  staging-like environment) and Helm refuses with an ownership-conflict
  error that doesn't obviously point at the real cause.
- **Pod Security Standards' `enforce` is namespace-wide, not
  per-workload** — there's no built-in way to enforce `restricted` for
  some pods and `privileged` for others in the same namespace. If a
  future workload doesn't need `hostPath`, that alone doesn't let you
  tighten `enforce` — every *other* pod in the namespace has to qualify
  too.

- **k3s enforces `NetworkPolicy` by default — it is NOT inert.** k3s embeds
  kube-router's NetworkPolicy controller *inside the k3s server binary
  itself* (not a separate pod — `kubectl get pods -A` shows nothing for
  it), active unless `--disable-network-policy` was passed at install time
  (it wasn't, here). `sudo iptables -L FORWARD -n -v` shows a live
  `KUBE-ROUTER-FORWARD` chain if you want to confirm. Every pod tier
  (`nginx`, `gateway`, `frontend`, `provisioner`, and dynamically-created
  `sandbox` pods) needs an explicit ingress-allow rule in
  `templates/networkpolicy-allow-tiers.yaml` or it's unreachable —
  including from pods that are themselves healthy and Ready. A missing
  rule here looks exactly like a broken network path (connection refused
  on every access method — NodePort, ClusterIP, even direct pod IP) while
  host-originated traffic (kubelet's own readiness/liveness probes) keeps
  working fine, which is what makes it easy to misdiagnose as a
  hairpin-NAT or CNI bug instead of a policy block. If a new pod tier is
  ever added to this chart, it needs its own allow-rule.
- **nginx's `resolver`-based dynamic DNS does not apply `/etc/resolv.conf`
  search domains.** `set $x upstream:port; proxy_pass http://$x;` (used
  here so nginx re-resolves per request instead of caching a stale IP
  forever) requires the **full Service FQDN**
  (`gateway.nova-staging.svc.cluster.local`), not the short name Compose's
  DNS would resolve fine. Short names 502 with "host not found" against
  CoreDNS.
- **nginx's default `proxy_read_timeout` is 60s.** Any location proxying
  to a blocking or SSE endpoint that can legitimately run longer than that
  (`/api/threads/*` — covers `/runs/wait` and `/runs/stream` — and the
  generic `/api/` fallback, which covers `/api/runs/wait` and
  `/api/runs/stream`) needs an explicit `proxy_read_timeout 600s;` or
  nginx kills the client connection with a 504 while the backend run keeps
  going, unaware the client is gone. This was a **real bug in production
  Compose's nginx.conf too**, not staging-only — fixed there in the same
  pass and already reloaded live.
- **`SKILLS_HOST_PATH` on the provisioner is a real node-filesystem path,
  not a path inside the provisioner's own container.**
  `docker/provisioner/app.py` embeds it verbatim as the `hostPath.path` of
  the *sandbox* pod it creates (`type: Directory`, no auto-create) — since
  sandbox pods schedule on the same node, it must resolve on that node's
  real filesystem. The provisioner container itself never reads this path;
  only the string value matters.
- **`fsGroup` does not cover static `hostPath`-backed PVs.** It only
  applies to volume plugins that support ownership management (dynamic
  `local-path` PVCs do); a statically-provisioned hostPath PV
  (`nova-deerflow-data`, chosen so sandbox pods and the gateway see
  identical host paths) stays root-owned regardless. `deployment-gateway.yaml`
  runs a one-shot root `chown` initContainer (`fix-deerflow-data-perms`)
  to work around this — confirmed by evidence (the `PermissionError`
  persisted even after adding `fsGroup`), not assumed from docs.
- **Kubernetes auto-injects legacy Docker-links env vars** —
  `{SERVICE_NAME}_PORT`, `{SERVICE_NAME}_SERVICE_HOST`, etc. — for every
  Service in the namespace into every pod. A Service named `gateway`
  injected `GATEWAY_PORT=tcp://10.43.x.x:8001`, colliding with and
  overriding the app's own `GATEWAY_PORT=8001` env var and crashing config
  parsing. `enableServiceLinks: false` is set on every pod spec in this
  chart for exactly this reason — don't remove it from a new Deployment.
- **The ResourceQuota requires every container, including
  initContainers, to declare `requests.cpu`/`requests.memory`/
  `limits.memory` explicitly**, or pod creation is silently rejected (only
  visible via `kubectl get events`, not `kubectl get pods`).

## Scaling

`gateway` and `frontend` are the only horizontally-scaled tiers (nginx,
provisioner, redis, and channels stay single-replica by design — see
`k8s/ARCHITECTURE.md` §4 for exactly why channels can't scale and why
Redis doesn't need to). Each has a `HorizontalPodAutoscaler`
(`templates/hpa-gateway.yaml` / `hpa-frontend.yaml`), CPU-based (target
70% utilization), backed by metrics-server (already part of this k3s
install — `kubectl top pods -n nova-staging` confirms it's live).

```bash
kubectl get hpa -n nova-staging
```

`minReplicas`/`maxReplicas` (`values.yaml`'s `resources.gateway.replicas`/
`.maxReplicas`, same for `frontend`) are sized to fit under the namespace
`ResourceQuota` with headroom to spare, not picked arbitrarily — see the
comment in `values.yaml` for the exact math. Scale-up reacts fast
(30s stabilization — agent runs are bursty); scale-down is deliberately
slow (300s) to avoid flapping a replica in and out right as a new burst of
runs starts.

Manually forcing a scale-out (e.g. to test before traffic actually
justifies it) works the same as anywhere else, but the HPA will just
scale it back down again on its own schedule unless you also bump
`minReplicas`:

```bash
kubectl scale deployment/gateway -n nova-staging --replicas=3
```

Zero-downtime is enforced structurally, not just hoped for: `gateway`'s
`RollingUpdate` strategy is `maxUnavailable: 0, maxSurge: 1` and it has a
`PodDisruptionBudget` (`minAvailable: 1`) — verified live by running
`kubectl rollout restart deploy/gateway` with an active SSE chat stream
open and confirming zero dropped requests.

### Multi-node (designed for, not deployed)

This box is a single Ryzen 5 3600 / 30GB RAM node, shared with unrelated
tenants (`gvm`, `fox-hosting`) — there is no second node to actually
schedule onto today, and standing up fake multi-node infra just to
exercise these fields wasn't judged worth it. What's already in place so
it activates cleanly the moment a real second node joins the cluster,
with no chart change required:

- **`topologySpreadConstraints`** on both `gateway` and `frontend`
  (`maxSkew: 1`, `topologyKey: kubernetes.io/hostname`,
  `whenUnsatisfiable: ScheduleAnyway`) — a genuine no-op on one node
  (there's nothing to spread across), but spreads replicas evenly once a
  second node exists. `ScheduleAnyway` (soft) rather than `DoNotSchedule`
  (hard) deliberately — a skew violation should never block a pod from
  scheduling on this small a cluster.
- **`nova-deerflow-data`'s `ReadWriteOnce` PV** would need to become
  `ReadWriteMany` (backed by NFS or similar) on a real second node —
  RWO is node-scoped, not pod-scoped, and today's multi-pod mounting only
  works because every pod in this single-node cluster happens to land on
  the same node RWO is scoped to (see `pvc-deerflow-data.yaml`'s comment).
  This is the one genuine architectural blocker to real multi-node — not
  fixed here, since it needs an actual second node's storage topology to
  design against correctly rather than guess at.
- **Node selectors / taints** for a future dedicated sandbox-execution
  node pool or GPU inference pool: intentionally not even scaffolded —
  correct fields with nothing to schedule onto are cheap; a node-pool
  design with no real node pool to validate against risks encoding wrong
  assumptions. Add when the second node's actual role is known.
- **`VerticalPodAutoscaler`**: explicitly skipped, not deferred — there's
  no competing workload variance on one node to observe and tune against.

## GitOps (Flux CD)

`k8s/flux/staging/` holds a `GitRepository` (this repo, `main` branch) +
`HelmRelease` (this chart, `values` mirroring `values-staging.yaml`) +
`Kustomization` tying them together. Once bootstrapped, Flux reconciles
`nova-staging` against whatever's on `main` — push a change to
`k8s/charts/nova/**` or to the `HelmRelease`'s inlined `values:` block,
and it lands on the cluster within the 1-minute poll interval, no manual
`helm upgrade` needed.

**Bootstrap** (one-time, human-run — needs a GitHub PAT with `repo`
scope, since it writes a deploy key back to the repo):

```bash
export PATH="$HOME/.local/bin:$PATH"   # flux CLI
export GITHUB_TOKEN=<your PAT>
flux bootstrap github --owner=<your-github-user> --repository=nova \
  --path=k8s/flux/staging --personal
```

**Verifying it's actually live** (not just installed):

```bash
flux get helmreleases -n nova-staging
```

Real proof it's live: edit a resource limit in the `HelmRelease`'s
`values:` block, push to `main`, and confirm the change lands on the
Deployment within the poll interval — without running `helm upgrade`
yourself.

Secrets stay **out** of Git either way — `bootstrap-secrets.sh` keeps
creating `nova-secrets` directly via `kubectl`, run manually per
namespace; the `HelmRelease` only ever references it by name.

**Rollback**: `flux suspend helmrelease nova -n nova-staging` (stops
reconciliation) + `git revert` the offending commit + `flux resume` — or
the manual escape hatch, `helm rollback nova <revision> -n nova-staging`,
which works regardless of Flux's state since it's a direct Helm
operation.

Flux itself is ~150-250MB resident (controller-only, no built-in UI) —
chosen over Argo CD specifically for that footprint on a box with real
other tenants; see `k8s/ARCHITECTURE.md`'s Phase 3 notes for the full
reasoning.

## CI/CD

`.github/workflows/container.yaml` builds the backend/frontend release
images (triggered on `v*` tags) and, before anything reaches GHCR:

1. Builds locally (`load: true`, not pushed yet).
2. Trivy scans the local image twice — an informational CRITICAL+HIGH
   report (always visible in the Action's logs) and a blocking
   CRITICAL-only gate (`exit-code: 1`, fails the job). HIGH is reported,
   not yet blocking, until the current backlog is triaged.
3. Only after both scans pass does it push, so the digest that gets
   attested/signed below is exactly the digest that was scanned.
4. SBOM (CycloneDX) generated and attested via `actions/attest-sbom` —
   same Sigstore-backed mechanism as the pre-existing build-provenance
   attestation, just a second predicate type.
5. cosign signs the pushed digest keylessly, using the same GitHub OIDC
   token already trusted for the attestations above (no new secret).

Verifying a released image:

```bash
cosign verify <image>@<digest> \
  --certificate-identity-regexp 'https://github.com/<owner>/nova/.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Deploy verification (rollout health checks, auto-rollback on a bad
deploy) is intentionally not built yet — noted as a natural Flux
follow-up once there's real rollout history to test a rollback against,
not built speculatively before there's anything to roll back.

## Security

- **Secrets**: `bootstrap-secrets.sh` bootstraps every secret-shaped env
  var this codebase actually reads (48 keys — LLM/search providers,
  Stripe, BYOK, IM channel credentials, tracing, `DATABASE_URL`), plus 5
  freshly-generated staging-only values (session/auth secrets never
  reused from prod's real `.env`). Re-run it any time `.env` gains a new
  key this script doesn't yet know about — it's a curated list, not a
  blind `.env` passthrough (see the script's own header comment for why
  a blind passthrough would be actively dangerous here).
- **Pod Security Standards**: the `nova-staging` namespace is labeled
  honestly, not aspirationally (`templates/namespace.yaml`) —
  `enforce: privileged` because `gateway`/`channels`' `skillsHostPath`
  `hostPath` mount is forbidden under *both* `baseline` and `restricted`
  (confirmed against the upstream PSS spec, not assumed), so enforcing
  either would reject those pods outright. `warn`/`audit` at `restricted`
  give real, non-blocking visibility — `kubectl apply`/`helm upgrade`
  prints exactly which control each pod would violate:

  ```bash
  kubectl get events -n nova-staging --field-selector reason=FailedCreate
  ```

- **Container hardening**: every container across all seven workloads
  (gateway, channels, frontend, nginx, provisioner, redis, the backup
  CronJob) runs with `allowPrivilegeEscalation: false`,
  `capabilities: { drop: ["ALL"] }`, and `readOnlyRootFilesystem: true`
  (with an explicit, audited `emptyDir`/PVC mount for whatever that
  specific container actually needs to write — see
  `k8s/ARCHITECTURE.md` §9 for the three real bugs this surfaced and how
  each was fixed). `seccompProfile: RuntimeDefault` on every pod spec.
- **NetworkPolicy**: real and enforced by k3s's embedded kube-router
  controller (not inert — see the gotcha below), default-deny ingress
  with an explicit allow-rule per pod tier. A new Deployment with no
  matching rule in `templates/networkpolicy-allow-tiers.yaml` is
  unreachable, not permissively open — this is a hard requirement for any
  new workload added to this chart, not a nice-to-have.

## Disaster Recovery

**Backup**: `templates/cronjob-backup.yaml` runs hourly, backing up
`deerflow.db` via SQLite's own online `.backup()` API (safe against a
live database with concurrent writers — a raw file copy of a WAL-mode DB
can catch a mid-write, inconsistent state) plus `extensions_config.json`,
tarred into `values.yaml`'s `backup.hostPath` with rolling retention
(`backup.retentionCount`, default 72 = 3 days of hourly backups).

**Honest limitation**: the backup destination is a hostPath on the *same
physical node* as the cluster it backs up — there's no second node or
object storage configured in this single-node setup. This protects
against a bad deploy, data corruption, or operator error; it does **not**
protect against a whole-node hardware failure, which takes the live data
and every backup together. Fixing this for real needs either a second
node or object storage — not built speculatively before either exists.

**RPO**: bounded by the backup interval — 1 hour.

**RTO**: measured via a real restore drill, not guessed. Real steps (this
is exactly what was run to produce the ~8 minute number below):

```bash
# 1. Pick a backup and extract it
tar -xzf /var/lib/nova-staging/backups/nova-backup-<timestamp>.tar.gz -C /tmp/restore

# 2. Place the restored DB where a fresh PV will point
mkdir -p /path/to/drill-data/deer-flow/.deer-flow/data
cp /tmp/restore/<timestamp>/deerflow.db /path/to/drill-data/deer-flow/.deer-flow/data/

# 3. Stand up a scratch namespace pointed at the restored data — the PV
#    name and hostPath must both be overridden (see pvc-deerflow-data.yaml's
#    comment for why the default can't just be reused across releases)
kubectl create namespace nova-dr-drill
k8s/scripts/bootstrap-secrets.sh nova-dr-drill .env
helm install nova-dr-drill k8s/charts/nova -n nova-dr-drill \
  -f k8s/charts/nova/values-staging.yaml \
  --set namespace=nova-dr-drill \
  --set nodePort=30027 \
  --set deerflowDataPVName=nova-dr-drill-deerflow-data-pv \
  --set deerflowDataHostPath=/path/to/drill-data \
  --set backup.hostPath=/path/to/drill-backups

# 4. Verify against the REAL restored data through the real application,
#    not just a raw file check — log in as a user known to exist in the
#    backup and confirm a genuine authenticated session comes back
curl -s -c /tmp/drill-cookies http://127.0.0.1:30027/api/v1/auth/register \
  -H "Content-Type: application/json" -d '{"email":"...", "password":"..."}'
curl -s -b /tmp/drill-cookies http://127.0.0.1:30027/api/v1/auth/me

# 5. Tear down
helm uninstall nova-dr-drill -n nova-dr-drill
kubectl delete namespace nova-dr-drill
```

Measured result: **~8 minutes** from "namespace doesn't exist" to
"verified working authenticated session against the restored data,"
including catching and fixing a real chart bug along the way (hardcoded,
non-namespaced `ClusterRole`/`ClusterRoleBinding`/`PersistentVolume`
names that collided across releases — see `k8s/ARCHITECTURE.md` §9
bug #14). The live `nova-staging` release was completely unaffected
throughout — this is exactly why the drill runs in a scratch namespace
instead of against the real one.

## Host watchdog (k3s cron)

`k8s/scripts/k3s-watchdog.py` is a short-lived script (not a long-running
daemon like `nova-healthcheck`) meant to run on a cron schedule, checking
whether **k3s itself** is healthy — independent of anything the K8s API
can report, since if the API server is down, the K8s-backed `/infra`
dashboard can't tell you why. This one runs on the host, outside the
cluster.

Checks each run: `systemctl is-active k3s`, node `Ready` condition, root
filesystem usage, and available memory. Writes a single JSON status file
(default `~/.nova/k3s-watchdog-status.json`) that nova-ops's `/infra` page
reads **directly off disk** — nova-ops runs on this same host via PM2, so
this is a plain same-host file read, not a gateway round-trip; it isn't
Nova application data.

**Install** (already done on this box — documented for a new box or a
disaster recovery from scratch):

```bash
# 1. Cron entry, every 5 minutes (cron has no CWD context — use an absolute path)
(crontab -l 2>/dev/null; echo "*/5 * * * * /usr/bin/python3 /home/jahanzaib/Desktop/nova/k8s/scripts/k3s-watchdog.py >> ~/.nova/k3s-watchdog.log 2>&1") | crontab -

# 2. Scoped sudoers rule for the one auto-fix action (restart k3s if it's
#    down or the node has gone NotReady) — mirrors the existing
#    cloudflared-nova.service pattern in scripts/healthcheck-daemon.py.
#    This needs to be added by a human with root, same as any sudoers change:
echo 'jahanzaib ALL=(root) NOPASSWD: /usr/bin/systemctl restart k3s' | sudo tee /etc/sudoers.d/nova-k3s-watchdog
sudo chmod 0440 /etc/sudoers.d/nova-k3s-watchdog
sudo visudo -c   # validates every file under /etc/sudoers.d, not just this one
```

Without step 2, the watchdog still runs, still writes status, and still
surfaces problems in the nova-ops UI — it just can't self-heal a downed
k3s service (the restart attempt fails with a permission error, logged
in `action_history` like any other outcome).

Auto-restart has a 10-minute cooldown (`K3S_WATCHDOG_RESTART_COOLDOWN_S`)
so a persistent, non-transient failure doesn't turn into a restart loop —
after one attempt, it waits and lets a human look, rather than hammering
`systemctl restart` every 5 minutes forever.

## Firewalling the NodePort

The nginx Service is `NodePort` (fixed `30026`), which listens on **all**
node interfaces (v4 and v6 — this box has live public IPv6 addresses) by
default, not just loopback/LAN. A plain `iptables INPUT`-chain rule
does **not** firewall this correctly: kube-proxy's NAT rewrites the
destination to a pod IP in the `nat` table before the packet ever reaches
`INPUT` (once DNAT'd away from a local address, it's forwarded traffic,
not input traffic) — so an `INPUT` rule for the NodePort silently does
nothing. The `raw` table's `PREROUTING` chain runs before any NAT and
kube-proxy never touches it, so it's the reliable place to filter by
source IP and survives kube-proxy's periodic rule sync:

```bash
sudo iptables -t raw -A PREROUTING -p tcp --dport 30026 -s 127.0.0.1 -j ACCEPT
sudo iptables -t raw -A PREROUTING -p tcp --dport 30026 -s 192.168.18.0/24 -j ACCEPT
sudo iptables -t raw -A PREROUTING -p tcp --dport 30026 -j DROP

sudo ip6tables -t raw -A PREROUTING -p tcp --dport 30026 -s ::1 -j ACCEPT
sudo ip6tables -t raw -A PREROUTING -p tcp --dport 30026 -j DROP
```

These rules are **not persisted across reboot** yet (no
`iptables-persistent`/equivalent installed — deliberately not added
without asking, since this is a shared box and package installs affecting
the firewall deserve an explicit decision). Re-apply after a reboot, or
set up persistence separately if this deployment becomes longer-lived.

## Resource ceilings

Every Deployment has both `requests` and `limits` (cgroup v2 makes
`limits` a real enforced ceiling). A namespace-wide `ResourceQuota`
(3 CPU / 3Gi requests, 8Gi limits — bumped from the original ~2 CPU/2Gi
in Phase 2 alongside the gateway replicas 1→2 change and the two new
Redis/channels workloads) is a hard backstop protecting the rest of the
box — this is a genuinely shared host also running an unrelated Compose
stack and other tenants. Sized with headroom for the gateway/frontend
HPAs to actually scale to their `maxReplicas` (4 each) without hitting the
quota — see `values.yaml`'s comment for the exact math. Sandbox pods are
governed by the provisioner's own pod spec (100m/256Mi requests, 1000m/1Gi
limits each), not this chart's quota — they still count against the same
node's real resources, just not against this namespace's quota math.

## Layout

```
k8s/
  README.md                  # this file
  ARCHITECTURE.md             # evidence-based architecture doc — every service, secret,
                               # state boundary, and every real bug found/fixed building this
  flux/staging/                # GitOps: GitRepository + HelmRelease + Kustomization
  scripts/
    build-images.sh          # docker build (gateway/frontend/provisioner) + ctr import
    bootstrap-secrets.sh     # bootstraps nova-secrets (48 keys) for a given namespace + .env
  charts/nova/
    Chart.yaml
    values.yaml               # generic defaults (HPA maxReplicas, backup retention, etc.)
    values-staging.yaml       # namespace, nodePort, image tags, skillsHostPath, host paths
    files/                    # config.yaml / nginx.conf / extensions_config.default.json /
                               # backup.py — copies, not symlinks
    templates/                # see `kubectl get all -n nova-staging` for what each renders;
                               # notable non-obvious ones: namespace.yaml (Pod Security
                               # Standards labels), cronjob-backup.yaml (Phase 7 DR),
                               # hpa-*.yaml / poddisruptionbudget-*.yaml (Phase 5 scaling)
```

## Local dev vs. this deployment vs. production

- **Local dev / Docker Compose**: `make dev` / `make docker-start` from the
  repo root — unrelated to everything in this directory, still the
  primary day-to-day workflow, untouched by any of this.
- **This deployment (single-node K3s staging)**: everything above —
  `nova-staging` namespace, real K8s-native sandbox pods, isolated
  storage/secrets/network from both Compose and any future production
  cutover.
- **Multi-node K3s**: designed for in specific, narrow places
  (`topologySpreadConstraints`, the `ReadWriteOnce`→`ReadWriteMany`
  question already called out) but not deployed — see the Multi-node
  section above for exactly what's real today vs. what activates later.
- **Production**: a separate, later, explicit cutover decision — this
  whole tree is staging-only until that decision is made. The live
  Docker Compose production stack has been unaffected by every phase of
  building this, verified after each one.
