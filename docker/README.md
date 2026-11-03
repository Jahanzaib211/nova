# docker/

Compose files for running Nova in containers. The self-hosted public
deployment is driven by PM2: the `nova` app execs `scripts/pm2-nova.sh`, which
runs `docker compose … up --no-build` and stays attached. `NOVA_STACK` (process
env, else `NOVA_STACK=` in `.env`, else `dev`) picks the chain:

| `NOVA_STACK` | Files | Gateway |
|---|---|---|
| `dev` (default) | `docker-compose-dev.yaml` + `dood` + `prod-frontend` | `dev` image, `uv sync` on boot, `uvicorn --reload` on the working tree |
| `prod` | `docker-compose.nova-prod.yaml` | baked `nova-gateway-prod` image, no reload watcher, uid 1000 |

Both chains add the same opt-in overlays: `docker-compose.voice.yaml` (speech
enabled and weights present; GPU voice is dev-only), `docker-compose.local-llm.yaml`
(`NOVA_LOCAL_LLM=1` and an nvidia runtime), and for `NOVA_ACP_AGENTS=1`
`docker-compose.cli-auth.yaml` plus `docker-compose.acp.yaml` (dev) or
`docker-compose.nova-prod.acp.yaml` (prod). A host still on the pre-rename
volumes also gets `docker-compose.legacy-names.yaml`.

## Switching the public deployment to the prod stack

**Why.** On the dev stack the gateway is `uvicorn --reload` on the working
tree: saving any backend `.py` file restarts production, and the restart stops
every sandbox and cancels in-flight runs (a live user run died this way on
2026-11-01). The prod stack serves a baked image; editing the checkout does
nothing until an image is rebuilt and the stack restarted on purpose.

**Expected downtime: about 1–3 minutes of 502s**, once. `pm2 restart nova`
stops the attached `compose up` and starts the prod chain; compose recreates
every service whose definition changed (gateway, jobs and frontend always —
new images; postgres, nginx and the web services too when their config
changed), so Postgres restarts briefly. The gateway then runs the schema step
and imports the app with no `uv sync`; it is healthy well inside its 60 s
`start_period`. Every in-flight run and every live sandbox ends — exactly what a
dev-stack reload does, but once and at a time you chose.

### 0. Preconditions (do these days before, not during)

```bash
cd ~/Desktop/nova

# a) Build the images. Side-effect free: they are tagged nova-gateway-prod /
#    nova-frontend-prod, never the dev stack's nova-gateway / nova-frontend.
docker compose -f docker/docker-compose.nova-prod.yaml -p nova build

# b) Smoke the gateway image without touching the stack (no network, no socket).
#    Importing the app parses config.yaml, which needs every $VAR it names;
#    config.example.yaml needs none. PYTEST_CURRENT_TEST keeps the sandbox
#    provider from adopting live sandboxes (backend/CLAUDE.md).
docker run --rm --network none --user 1000:1000 -e HOME=/tmp --tmpfs /tmp \
  -e PYTEST_CURRENT_TEST=1 -e DATABASE_URL=postgresql://u:p@nowhere:5432/d \
  -e NOVA_CONFIG_PATH=/app/backend/config.yaml \
  -v "$PWD/config.example.yaml:/app/backend/config.yaml:ro" \
  -v "$PWD/extensions_config.json:/app/backend/extensions_config.json:ro" \
  nova-gateway-prod:latest \
  sh -c 'cd backend && PYTHONPATH=. uv run --no-sync python -c "import app.gateway.app, app.jobs.worker, asyncpg"' \
  && echo smoke ok

# c) A stable JWT secret. The launcher REFUSES the prod stack without one.
#    Reuse the persisted value so nobody is signed out by the switch:
grep -q '^AUTH_JWT_SECRET=' .env || \
  printf 'AUTH_JWT_SECRET=%s\n' "$(cat backend/.nova/.jwt_secret)" >> .env

# d) File ownership. The dev gateway runs as root and has left root-owned
#    directories under backend/.nova (per-user dirs, memory.json, threads);
#    the prod gateway runs as uid 1000 and cannot write them. The launcher
#    refuses the prod stack while any exist. Count, then fix:
sudo find backend/.nova skills ! -uid 1000 | wc -l
sudo chown -R 1000:1000 backend/.nova skills

# e) Dry-run the launcher's preflight for the prod stack (read-only). It must
#    end without "REFUSING"; warnings are worth reading too.
python3 scripts/stack_preflight.py --stack prod
```

The dev stack keeps working after (c) and (d): it runs as root, and the secret
it reads is the same one it already used.

### 1. Switch

```bash
# Nothing running? (SELECT only)
docker exec nova-postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \
  \"select count(*) from runs where status in ('pending','running')\""

# Durable: the launcher reads NOVA_STACK from .env on every start and reboot.
sed -i '/^NOVA_STACK=/d' .env && echo 'NOVA_STACK=prod' >> .env
pm2 restart nova
pm2 logs nova --lines 50          # watch for [preflight] lines and compose errors
```

### 2. Verify

```bash
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'   # gateway/jobs on nova-gateway-prod, all (healthy)
curl -fsS http://127.0.0.1:8001/health                     # 200
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:2026/   # 200
docker exec nova-gateway ps -o pid,args | grep -c -- --reload      # 0
```

Then sign in on the public URL (existing sessions should still be valid), send
one chat turn, and if voice/ACP are enabled try one of each. Gateway logs are
now `docker logs nova-gateway` (json-file, rotated), not `logs/gateway.log`.

### Rollback

```bash
sed -i '/^NOVA_STACK=/d' .env      # back to the default, dev
pm2 restart nova
```

Same 1–3 minutes. Nothing about the data changes between stacks: same project
(`nova`), network, pinned addresses, `nova_nova-postgres-data` volume and
`backend/.nova` bind mount; both gateways run the same idempotent schema step.
The dev images are untouched by the prod build, so rollback needs no rebuild.
The `chown` in step 0 does not need undoing — the dev gateway is root.

### Deploying code on the prod stack

```bash
git pull
docker compose -f docker/docker-compose.nova-prod.yaml -p nova build
pm2 restart nova
```

Pin a known-good build with `NOVA_PROD_IMAGE_TAG` (e.g. the short git sha) in
`.env` before building if you want instant rollback to the previous image.

### Known differences from the dev stack

* Voice runs on CPU (`NOVA_PROD_UV_EXTRAS`, default `postgres,trading,voice`).
  GPU voice needs `voice-gpu` plus the CUDA 12 wheels `make voice-libs`
  installs out of band, which no image carries.
* ACP: the Claude adapter's npm cache lives in a tmpfs at `/root`, so the first
  Claude Code turn after each gateway start downloads the adapter again.
* No source bind mounts: `config.yaml` and `extensions_config.json` are still
  bind-mounted (read-only); everything else is in the image.
