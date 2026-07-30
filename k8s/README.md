# Nova on K8s (staging)

A single-node k3s deployment of Nova (gateway + frontend + nginx + sandbox
provisioner) running in the `nova-staging` namespace, fully isolated from
the live Docker Compose production stack on the same box (separate
runtime, network, storage, and freshly-generated secrets). Sandbox
execution runs as real K8s-native Pods via the provisioner instead of
Docker-out-of-Docker — `AioSandboxProvider` switches automatically when
`sandbox.provisioner_url` is set in `config.yaml`, no code change needed.

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
`config.yaml`, `nginx.conf`, `extensions_config.default.json`) does **not**
restart the pod that mounts it — Kubernetes updates the file on disk
in-place but the already-running process keeps its old in-memory state.
Follow up with an explicit rollout restart for whichever component owns
that file:

```bash
kubectl rollout restart deployment/gateway -n nova-staging   # config.yaml, env vars
kubectl rollout restart deployment/nginx    -n nova-staging   # nginx.conf
```

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
(~2 CPU / 2Gi requests, 6Gi limits) is a hard backstop protecting the rest
of the box — this is a genuinely shared host also running an unrelated
Compose stack and other tenants. Sandbox pods are governed by the
provisioner's own pod spec (100m/256Mi requests, 1000m/1Gi limits each),
not this chart's quota — they still count against the same node's real
resources, just not against this namespace's quota math.

## Layout

```
k8s/
  README.md                  # this file
  scripts/
    build-images.sh          # docker build (gateway/frontend/provisioner) + ctr import
    bootstrap-secrets.sh     # one-time: fresh session secrets + real API keys from ../.env
  charts/nova/
    Chart.yaml
    values.yaml               # generic defaults
    values-staging.yaml       # namespace, nodePort, image tags, skillsHostPath
    files/                    # config.yaml / nginx.conf / extensions_config.default.json — copies, not symlinks
    templates/                # see `kubectl get all -n nova-staging` for what each renders
```
