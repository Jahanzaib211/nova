# Deployment Guide

> How Nova gets from code to production.

**Audience:** operators, contributors deploying to the Nova host.
**Last Updated:** Phase C9 (2026-07-17)
**Related:** [docs/RUNBOOK.md](docs/RUNBOOK.md), [docs/MONITORING.md](docs/MONITORING.md), [backend/CLAUDE.md](backend/CLAUDE.md)

---

## Deployment Model

Nova runs as a single-process Gateway with an embedded agent runtime. The
standard deployment is a single host with Docker containers for nginx,
frontend, and the gateway — plus PM2 for watchdog and tunnel processes.

```
Cloudflare DNS → nova.alilabsx.com
    ↓
cloudflared (systemd) → localhost:2026
    ↓
nginx (Docker) → :2026
    ├─ /api/* → Gateway :8001 (Docker)
    └─ /*     → Frontend :3000 (Docker)
```

**K8s staging** (single-node k3s, `nova-staging` namespace, isolated from
the above — separate runtime, network, storage, secrets) also runs on this
box for validating the sandbox-provisioner path (real K8s Pods instead of
Docker-out-of-Docker) before any real cutover decision. Not part of the
production traffic path. See [k8s/README.md](k8s/README.md).

---

## Deploy Steps

### 1. Push to origin main

```bash
git push origin main
```

The host's git hook or manual pull triggers deployment.

### 2. Pull and restart

```bash
cd ~/Desktop/nova
git pull origin main
make install          # root + frontend deps
cd backend && make install && cd ..
make docker-start     # restart Docker containers
pm2 reload ecosystem.config.js  # restart PM2 processes
```

### 3. Verify

```bash
curl -s http://localhost:2026/health | python3 -m json.tool
curl -s https://nova.alilabsx.com/health | python3 -m json.tool
python3 scripts/check_platform_guardrails.py
```

---

## Database Migrations

If the pull includes a new Alembic migration:

```bash
# Inside the running gateway container
docker exec deer-flow-gateway python -c "
from packages.harness.deerflow.persistence.migrations.env import *
"
```

Verify:

- Live DB schema matches ORM model column count
- Alembic version is correct

See `docs/RUNBOOK.md` §8 for full migration workflow.

---

## PM2 Processes

`ecosystem.config.js` defines **four** apps:

| Process | Purpose |
|---------|---------|
| `nova` | The Docker compose stack (gateway, frontend, nginx, autoheal) |
| `nova-litellm` | LiteLLM proxy for free models |
| `nova-healthcheck` | 13-probe watchdog daemon |
| `nova-gates` | Refreshes the Nova Ops gate status files, and runs the checkpoint pruner and log rotation |

Four more are documented in `ecosystem.config.js`'s header as **deliberately
removed** on 2026-08-13, because every unstartable entry turned the watchdog's
"heal missing app" logic into an infinite repair loop:

| Removed | Why |
|---------|-----|
| `llama-bridge` | `~/Desktop/llama-bridge` does not exist |
| `nova-dify` | `~/Desktop/dify` does not exist |
| `nova-tunnel` | `cloudflared-nova.service` is not installed; the public hostname is served by the separate `tunnel-nova` PM2 app |
| `nova-monitoring` | Duplicates the live k3s `monitoring` namespace — see the note in `scripts/pm2-monitoring.sh` before re-enabling |

Do not re-add one without first making it actually startable.

Restart via:

```bash
export $(grep -v '^#' ~/.config/nova/monitoring.env | xargs) && \
  pm2 restart nova-monitoring
```

The compose stack also requires the env file at `~/.config/nova/monitoring.env` (see §3).

---

## Rollback

If a deployment breaks production:

1. `git log --oneline -5` — find the last good commit
2. `git revert <commit>` or `git reset --hard <commit>`
3. `git push origin main` (or force-push if needed)
4. Follow deploy steps above

---

## First-Time Host Setup

See `docs/RUNBOOK.md` §9 for fresh host setup:

- Docker + docker compose v2
- PM2 (`npm install -g pm2`)
- `make install` (root deps + frontend deps)
- `scripts/install-cloudflared-nova.sh` (systemd unit + sudoers + logrotate + PM2 entry)
- `pm2 start ecosystem.config.js` (or `pm2 reload` after editing)
- `pm2 save` (persist PM2 state across reboots)
- Cloudflare dashboard DNS: `nova.alilabsx.com` → tunnel CNAME
- Verify: `curl https://nova.alilabsx.com/health`

---

## Key Operational Facts (don't relearn these the hard way)

- **Frontend is prod-baked** (`next start` from the image, NOT hot-reload). UI changes
  require `docker compose build frontend` + `--force-recreate --no-deps frontend`.
  Never `rm -rf` the container's `.next`.
- **Gateway hot-reloads** mounted `backend/` source (uvicorn `--reload`) — editing
  routers auto-deploys to the live gateway serving 24 users. **Migrate the DB before
  changing ORM models** (an unmigrated ADD COLUMN crash-looped prod once).
- **Standing rule:** ask before killing/stopping/restarting ANY process or container.
- Credit wall is opt-in (`NOVA_CREDITS_ENFORCED`), graceful 402 with `NOVA_SUPPORT_CONTACT`.
- Full detail lives in `~/Desktop/nova/.opencode/plans/nova-consolidated-audit-2026-07.md`.
