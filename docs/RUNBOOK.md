# Nova Operations Runbook

This runbook covers the most common operational tasks on a Nova host.
Future operators should be able to use this document without tribal knowledge.

---

## 1. Health Checks

### Single-shot full probe

```bash
curl -s http://localhost:2026/health | python3 -m json.tool
# Expect: {"status":"healthy","service":"nova-gateway"}
```

### Layer-by-layer

```bash
# Nginx reverse proxy
curl -sI http://localhost:2026/ | head -1
# Expect: HTTP/1.1 200 OK

# Gateway
curl -sI http://localhost:8001/health | head -1
# Expect: HTTP/1.1 405 Method Not Allowed (uvicorn — only GET works, see below)

curl -s http://localhost:8001/health | python3 -m json.tool
# Expect: {"status":"healthy","service":"nova-gateway"}

# Frontend dev server
curl -sI http://localhost:3000/ | head -1
# Expect: HTTP/1.1 200 OK

# Cloudflare Tunnel (public)
curl -sI https://nova.alilabsx.com/health | head -1
# Expect: HTTP/2 405 (use GET)
curl -s https://nova.alilabsx.com/health | python3 -m json.tool
# Expect: {"status":"healthy","service":"nova-gateway"}
```

### Continuous watchdog

`nova-healthcheck` (PM2) runs `scripts/healthcheck-daemon.py` every 30 s. Twelve probes (P1–P12) cover nginx, gateway, frontend, the local LLM stack, and the Cloudflare Tunnel. Logs at `/home/jahanzaib/.pm2/logs/nova-healthcheck-out.log`. Auto-fixes are issued for known-good cases (see §5).

```bash
pm2 ls                              # process state
pm2 logs nova-healthcheck --lines 50 --nostream
```

---

## 2. Cloudflare Tunnel Recovery

### Symptom

Users report `Error 1033 — Cloudflare cannot resolve the tunnel`. The gateway is up; the public URL returns 502 from nginx because the upstream is unreachable from the tunnel's perspective.

### Diagnosis

```bash
systemctl status cloudflared-nova.service --no-pager -n 15
# Look for:
#   * Active: failed (Result: start-limit-hit) — restart loop exhausted
#   * Active: activating — slow start
#   * Active: active (running) — but edge can't reach us

systemctl is-active cloudflared-nova.service
# active | activating | inactive | failed

tail -n 50 /var/log/cloudflared/nova-out-$(date +%Y%m%d).log
# Look for: "no more connections active and exiting" + "Failed to dial a quic connection"
```

### Auto-recovery

The `nova-healthcheck` watchdog has a P12_tunnel probe with a built-in
fix path (`scripts/healthcheck-daemon.py::fix_tunnel`). It runs every
~30 s. The fix sequence is:

1. **Preflight**: `sudo -n systemctl reset-failed cloudflared-nova.service` to
   clear any `start-limit-hit` state. Without this, restart silently fails
   (this is what triggered the 2026-07-12 outage).
2. **Restart**: `sudo -n systemctl restart cloudflared-nova.service`.
3. **Verify**: poll `is-active` for up to 10 s. Only return success when
   the unit is genuinely `active`.

The fix only returns True after the unit is verified. If the watchdog's
next cycle still sees RED, it retries — no silent failure.

If the watchdog reports `sudoers is missing 'reset-failed cloudflared-nova'` in its logs, the operator must apply the sudoers update:

```bash
sudo bash /home/jahanzaib/Desktop/nova/scripts/install-cloudflared-nova.sh
# Re-runs sudo tee /etc/sudoers.d/nova-watchdog
```

### Manual recovery

If auto-recovery is failing or PM2 is down:

```bash
# 1. Confirm the failure mode
systemctl status cloudflared-nova.service

# 2. Clear start-limit-hit (this is the most common cause)
sudo systemctl reset-failed cloudflared-nova.service

# 3. Restart
sudo systemctl restart cloudflared-nova.service

# 4. Wait + verify (cloudflared takes ~3-5 s to register with edge)
sleep 5
systemctl is-active cloudflared-nova.service
# expect: active

# 5. End-to-end test
curl -s https://nova.alilabsx.com/health | python3 -m json.tool
# expect: {"status":"healthy","service":"nova-gateway"}
```

### Tunnel token rotation

```bash
# 1. Cloudflare dashboard → Zero Trust → Networks → Tunnels →
#    Nova hackathon → Configure → Regenerate token
# 2. Paste the new token into /etc/cloudflared/env (mode 0600)
sudo -e 'echo "TUNNEL_TOKEN=<new-token>" > /etc/cloudflared/env'
# 3. Restart
sudo systemctl restart cloudflared-nova.service
# 4. Verify
sleep 5
systemctl is-active cloudflared-nova.service
```

---

## 3. Container Restart Procedures

### Restart a single container

```bash
docker compose -p deer-flow-dev \
  -f /home/jahanzaib/Desktop/nova/docker/docker-compose-dev.yaml \
  up -d --no-deps --force-recreate <service>
# Common services: frontend, gateway, nginx
```

### Full stack restart

```bash
# Stop everything (preserves volumes + images)
docker compose -p deer-flow-dev \
  -f /home/jahanzaib/Desktop/nova/docker/docker-compose-dev.yaml \
  down

# Bring it all back
docker compose -p deer-flow-dev \
  -f /home/jahanzaib/Desktop/nova/docker/docker-compose-dev.yaml \
  up -d

# Verify the public URL is up
sleep 8
curl -s https://nova.alilabsx.com/health | python3 -m json.tool
```

### Startup order (Phase C0.1)

```
pm2 nova-tunnel (waits for /health on :2026 up to 90s, then starts tailing)
   ▲ depends on
   │
nginx + gateway (docker compose)
```

The PM2 `nova-tunnel` wrapper now waits for the local origin (`localhost:2026/health`)
to return 200 before emitting heartbeats. This prevents P12_tunnel from
flagging RED during the gateway's cold start. Bound to 90 s so PM2's
`max_restarts` kicks in if nginx is genuinely broken.

---

## 4. Deployment Procedure

### Standard workflow

```bash
cd /home/jahanzaib/Desktop/nova

# 1. Pull + checkout
git fetch origin
git checkout main

# 2. Validate before deploy
cd backend && .venv/bin/python -m pytest tests/ -x -q
cd ../frontend && pnpm typecheck && pnpm lint && pnpm test --run

# 3. Rebuild frontend container (build context is the repo root)
docker compose -p deer-flow-dev -f ../docker/docker-compose-dev.yaml \
  build --no-cache frontend
docker compose -p deer-flow-dev -f ../docker/docker-compose-dev.yaml \
  up -d --no-deps --force-recreate frontend

# 4. Restart gateway if backend changed
docker compose -p deer-flow-dev -f ../docker/docker-compose-dev.yaml \
  up -d --no-deps --force-recreate gateway

# 5. Reload nginx if config changed
docker compose -p deer-flow-dev -f ../docker/docker-compose-dev.yaml \
  up -d --no-deps --force-recreate nginx

# 6. Verify end-to-end
sleep 5
curl -s https://nova.alilabsx.com/health | python3 -m json.tool
```

### Rollback

```bash
# 1. Identify the last good commit
git log --oneline -20

# 2. Revert + redeploy
git revert <bad-commit-sha>      # or: git reset --hard <good-sha>
./deploy.sh                      # if using the deploy script
# Or manually:
docker compose -p deer-flow-dev -f docker/docker-compose-dev.yaml \
  up -d --no-deps --force-recreate frontend gateway nginx

# 3. Verify
sleep 5
curl -s https://nova.alilabsx.com/health | python3 -m json.tool
```

---

## 5. PM2 Operations

### Common commands

```bash
pm2 ls                                  # all processes
pm2 logs <name> --lines 100 --nostream  # recent logs
pm2 restart <name>                      # soft restart (keeps config)
pm2 reload <name>                       # cluster-mode zero-downtime
pm2 delete <name>                       # remove from PM2
pm2 save                                 # persist current state across reboots
```

### nova-tunnel (Phase C0.1)

The PM2 `nova-tunnel` process is an observability wrapper around the
systemd `cloudflared-nova.service` unit. It tails journald and emits a
30 s heartbeat. The wrapper's lock file lives in `$XDG_RUNTIME_DIR` (or
`/tmp`) — NOT in `/var/run` (root-owned, was breaking the wrapper).

If `pm2 ls` shows `nova-tunnel` as `errored` with rapid restart counts:
the lock file is unwritable. Check `/home/jahanzaib/.pm2/logs/nova-tunnel-error.log`
for `Permission denied`. The fix lands in `/tmp/cloudflared-nova-pm2.lock`
(previous path was `/var/run/cloudflared-nova-pm2.lock`).

### nova-healthcheck (Phase C0 watchdog)

Runs every 30 s. Auto-fixes known issues:

| Probe  | Failure mode                          | Auto-fix action |
| ------ | ------------------------------------- | --------------- |
| P1     | nginx down                            | restart nginx container |
| P2     | gateway unreachable                   | restart gateway container |
| P3     | frontend root not 200                 | restart frontend container |
| P4/P5/P6 | local LLM gateway / llama unreachable | restart llama-bridge |
| P7     | docker containers count low           | (warn, no fix) |
| P10    | LiteLLM down                          | pm2 restart nova-litellm |
| P11    | Dify stack down                       | pm2 restart nova-dify |
| **P12** | **cloudflared tunnel down**         | **systemctl reset-failed + restart (with verification)** |

---

## 6. Common Failure Modes

### "Cloudflare cannot resolve the tunnel" (Error 1033)

**Root cause**: cloudflared process is not registered with Cloudflare's
edge. Three sub-causes, in order of likelihood:

1. **systemd unit in `start-limit-hit`**: cloudflared crashed too many
   times. Run `sudo systemctl reset-failed cloudflared-nova.service`
   then restart. The watchdog does this automatically every 30 s when
   `fix_tunnel()` succeeds.
2. **cloudflared's QUIC connections to edge all dropped**: the binary
   exited cleanly ("no more connections active"). Network blip, edge
   rotation, or local DNS issue. Restart brings it back.
3. **Tunnel token expired or rotated**: re-apply via
   `scripts/install-cloudflared-nova.sh` with `TUNNEL_TOKEN=…`.

### "502 Bad Gateway" from nginx but gateway is up

**Root cause**: nginx can't reach the gateway. Usually means the gateway
container is on a different IP than nginx expects, or the gateway is
rebooting. Check `docker ps` — gateway's IP must match the upstream in
`docker/nginx/nginx.conf`.

### Frontend returns stale code

**Root cause**: The frontend container is running `pnpm run dev` with
the bind-mounted source. New edits should hot-reload via Turbopack. If
they don't, restart the frontend container. If still stale, the image
may have been rebuilt without a fresh `pnpm install` — full image rebuild:

```bash
docker compose -p deer-flow-dev -f docker/docker-compose-dev.yaml \
  build --no-cache frontend
docker compose -p deer-flow-dev -f docker/docker-compose-dev.yaml \
  up -d --no-deps --force-recreate frontend
```

### PM2 `nova-tunnel` rapid restart loop

**Root cause**: lock file is unwritable. The wrapper used to write to
`/var/run/cloudflared-nova-pm2.lock` (root-owned). Fixed in Phase C0.1:
now uses `$XDG_RUNTIME_DIR` (or `/tmp`).

### Gateway returns 401 unexpectedly

**Root cause**: `.env` has `DEER_FLOW_AUTH_DISABLED=0`. To restore
dev-mode auth skip, set `DEER_FLOW_AUTH_DISABLED=1` in `.env` and restart
the gateway container. Production should keep auth disabled=0.

---

## 7. Monitoring Expectations

| Signal | Expected value | Action if deviates |
| ------- | -------------- | ------------------- |
| `pm2 nova-tunnel uptime` | should keep growing | check `nova-tunnel-error.log` |
| `cloudflared-nova.service Active` | active (running) | follow §2 |
| `https://nova.alilabsx.com/health` | `{"status":"healthy",...}` | follow §2 |
| `systemctl is-active cloudflared-nova` | `active` | follow §2 |
| `pm2 nova-healthcheck uptime` | growing, low restart count | check daemon logs |
| `deer-flow-frontend Up` | minutes:hours, no restarts | check `logs/frontend.log` |
| `deer-flow-gateway Up` | minutes:hours, no restarts | check `logs/gateway.log` |

---

## 8. Alembic Database Migrations

Nova uses Alembic for schema migrations. The migration infrastructure is at
`backend/packages/harness/deerflow/persistence/migrations/`.

### When a migration is needed

Any ORM model change that affects the database schema (adding/removing columns,
changing types) requires a migration. Never rely on `Base.metadata.create_all()`
in production — it only creates tables, not columns.

### Creating a migration

1. Create `versions/YYYY_MM-DD_description.py` (idempotent):

```python
def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("runs")]
    if "new_column" not in columns:
        op.add_column("runs", sa.Column("new_column", sa.String(64), nullable=True))
    op.execute("INSERT OR REPLACE INTO alembic_version (version_num) VALUES ('2026_07_12_description')")
```

2. For fresh deployments, stamp the alembic version in the migration file.

### Verifying migration state

```bash
# Inside the running gateway container
docker exec deer-flow-gateway python -c "
import sqlite3; conn = sqlite3.connect('backend/data/deerflow.db');
print([c[1] for c in conn.execute('PRAGMA table_info(runs)').fetchall()]);
print(conn.execute('SELECT * FROM alembic_version').fetchall())
"
```

---

## 9. First-Time Setup

A fresh Nova host needs:

1. Docker + docker compose v2
2. PM2 (`npm install -g pm2`)
3. `make install` (root deps + frontend deps)
4. `scripts/install-cloudflared-nova.sh` (systemd unit + sudoers + logrotate + PM2 entry)
5. `pm2 start ecosystem.config.js` (or `pm2 reload` after editing)
6. `pm2 save` (persist PM2 state across reboots)
7. Cloudflare dashboard DNS: `nova.alilabsx.com` → tunnel CNAME
8. Verify: `curl https://nova.alilabsx.com/health`

See `docs/MONITORING.md` for observability stack setup and `docs/INSTALL.md`
for full onboarding.

---

## 10. Contact / Escalation

- Cloudflare status: <https://www.cloudflarestatus.com>
- Tunnel documentation: <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/>
- Nova issue tracker: <https://github.com/Jahanzaib211/nova/issues>
