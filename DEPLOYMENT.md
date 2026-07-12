# Deployment Guide

> How Nova gets from code to production.

**Audience:** operators, contributors deploying to the Nova host.
**Last Updated:** Phase C1 (2026-07-12)
**Related:** [docs/RUNBOOK.md](docs/RUNBOOK.md), [docs/MONITORING.md](docs/MONITORING.md), [backend/CLAUDE.md](backend/CLAUDE.md)

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

## PM2 Processes

| Process | Script | Purpose |
|---------|--------|---------|
| nova | ecosystem.config.js | Main gateway (managed via Docker) |
| llama-bridge | ecosystem.config.js | Local LLM bridge |
| nova-litellm | ecosystem.config.js | LiteLLM proxy for free models |
| nova-dify | ecosystem.config.js | Dify stack |
| nova-healthcheck | ecosystem.config.js | 12-probe watchdog daemon |
| nova-tunnel | ecosystem.config.js | Cloudflare tunnel wrapper |
| nova-monitoring | ecosystem.config.js | Monitoring stack |

## Rollback

If a deployment breaks production:

1. `git log --oneline -5` — find the last good commit
2. `git revert <commit>` or `git reset --hard <commit>`
3. `git push origin main` (or force-push if needed)
4. Follow deploy steps above

## First-Time Host Setup

See `docs/RUNBOOK.md` §9 for fresh host setup:
- Docker + docker compose v2
- PM2
- cloudflared (systemd + sudoers)
- Cloudflare dashboard DNS
