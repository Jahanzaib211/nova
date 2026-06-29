---
name: nova-deploy-local
description: Run Nova locally end-to-end (Docker Compose + PM2 + dev-entrypoint.sh). Use when the user says "how do I run this", "start Nova", "make dev", "spin it up locally", or "I just cloned the repo". Goes through pre-flight (Docker, PM2, uv, pnpm checks) → `make dev` → nginx :2026 + gateway :8001 + frontend :3000 → verification (curl `/`, curl `/health`, curl `/workspace/chats/<id>`). The canonical local dev path: `make dev` starts `docker compose -f docker/docker-compose-dev.yaml -f docker/docker-compose.dood.yaml up` under PM2 supervision. Triggers on phrases like "run Nova locally", "make dev", "I just cloned the repo", "first time setup".
---

# nova-deploy-local

## Pre-flight (5 min)

```bash
# Required tools
docker --version         # Docker engine running
which uv && uv --version  # uv for Python
which node && node --version  # Node 22+ for frontend
which pnpm                # pnpm for frontend deps
which pm2 && pm2 --version  # PM2 for process supervision
which gh && gh --version  # For the post-deploy tag/release
```

## Deploy

```bash
# 1. Configure (only on first run; config.example.yaml is the template)
cp config.example.yaml config.yaml
cp .env.example .env
# Edit .env: set DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=http://gateway:8001
# Edit config.yaml: confirm models are configured (or use the default OpenAI-compatible stub)

# 2. Install dependencies
make install
# OR, more granularly:
cd backend && uv sync --all-packages
cd frontend && pnpm install

# 3. Start (foreground — use this for dev; Ctrl-C to stop)
make dev
# OR, supervised (foreground; PM2 will restart on crash):
pm2 start ecosystem.config.js
# OR, daemonized (background):
./scripts/start-daemon.sh
```

## Verify

```bash
# All three ports should respond
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:2026/        # 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/health  # 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/        # 200

# End-to-end through nginx
curl -s -o /dev/null -w "%{http_code}\n" \
    http://localhost:2026/workspace/chats/bbb04f5d-1e96-4211-bbc2-01972b7ac13e  # 307 (Next.js redirect)
```

## Stop

```bash
pm2 stop deerflow
# OR
./scripts/serve.sh --stop
# OR
docker compose -f docker/docker-compose-dev.yaml -f docker/docker-compose.dood.yaml -p deer-flow-dev down
```

## Common issues

- **Port 2026 already in use:** `lsof -i :2026`; kill the process or change nginx port
- **Config.yaml not found:** the gateway app raises `FileNotFoundError: 'config.yaml' file not found in the project root or legacy backend/repository root locations` (per `app_config.py:205`)
- **AIO container won't start:** `docker logs deer-flow-aio-{uuid} --tail 30`; check `enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest` is pullable
- **PM2 doesn't restart on crash:** `pm2 status deerflow`; check `ecosystem.config.js` for `autorestart: true`

## Anti-patterns

- ❌ **Don't** run `uvicorn app.gateway.app:app` directly — bypasses PM2 supervision and the dood overlay
- ❌ **Don't** edit `config.yaml` while Nova is running — mtime-based hot reload exists but doesn't cover all fields
- ❌ **Don't** use `docker compose down -v` — deletes the AIO container cache
