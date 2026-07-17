# Nova Operations Runbook

This runbook covers the most common operational tasks on a Nova host.
Future operators should be able to use this document without tribal knowledge.

---

## 1. Health Checks

### Single-shot full probe

```bash
curl -s http://localhost:2026/health | python3 -m json.tool
# Expect: {"status":"healthy","service":"deer-flow-gateway"}
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
# Expect: {"status":"healthy","service":"deer-flow-gateway"}

# Frontend dev server
curl -sI http://localhost:3000/ | head -1
# Expect: HTTP/1.1 200 OK

# Cloudflare Tunnel (public)
curl -sI https://nova.alilabsx.com/health | head -1
# Expect: HTTP/2 405 (use GET)
curl -s https://nova.alilabsx.com/health | python3 -m json.tool
# Expect: {"status":"healthy","service":"deer-flow-gateway"}
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

The `nova-healthcheck` watchdog has a P12_tunnel probe with a built-in fix path (`scripts/healthcheck-daemon.py::fix_tunnel`). It runs every ~30 s. The fix sequence is:

1. **Preflight**: `sudo -n systemctl reset-failed cloudflared-nova.service` to clear any `start-limit-hit` state. Without this, restart silently fails (this is what triggered the 2026-07-12 outage).
2. **Restart**: `sudo -n systemctl restart cloudflared-nova.service`.
3. **Verify**: poll `is-active` for up to 10 s. Only return success when the unit is genuinely `active`.

The fix only returns True after the unit is verified. If the watchdog's next cycle still sees RED, it retries — no silent failure.

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
# expect: {"status":"healthy","service":"deer-flow-gateway"}
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

The PM2 `nova-tunnel` wrapper now waits for the local origin (`localhost:2026/health`) to return 200 before emitting heartbeats. This prevents P12_tunnel from flagging RED during the gateway's cold start. Bound to 90 s so PM2's `max_restarts` kicks in if nginx is genuinely broken.

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

The PM2 `nova-tunnel` process is an observability wrapper around the systemd `cloudflared-nova.service` unit. It tails journald and emits a 30 s heartbeat. The wrapper's lock file lives in `$XDG_RUNTIME_DIR` (or `/tmp`) — NOT in `/var/run` (root-owned, was breaking the wrapper).

If `pm2 ls` shows `nova-tunnel` as `errored` with rapid restart counts: the lock file is unwritable. Check `/home/jahanzaib/.pm2/logs/nova-tunnel-error.log` for `Permission denied`. The fix lands in `/tmp/cloudflared-nova-pm2.lock` (previous path was `/var/run/cloudflared-nova-pm2.lock`).

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

**Root cause**: cloudflared process is not registered with Cloudflare's edge. Three sub-causes, in order of likelihood:

1. **systemd unit in `start-limit-hit`**: cloudflared crashed too many times. Run `sudo systemctl reset-failed cloudflared-nova.service` then restart. The watchdog does this automatically every 30 s when `fix_tunnel()` succeeds.
2. **cloudflared's QUIC connections to edge all dropped**: the binary exited cleanly ("no more connections active"). Network blip, edge rotation, or local DNS issue. Restart brings it back.
3. **Tunnel token expired or rotated**: re-apply via `scripts/install-cloudflared-nova.sh` with `TUNNEL_TOKEN=…`.

### "502 Bad Gateway" from nginx but gateway is up

**Root cause**: nginx can't reach the gateway. Usually means the gateway container is on a different IP than nginx expects, or the gateway is rebooting. Check `docker ps` — gateway's IP must match the upstream in `docker/nginx/nginx.conf`.

### Frontend returns stale code

**Root cause**: The frontend container is running `pnpm run dev` with the bind-mounted source. New edits should hot-reload via Turbopack. If they don't, restart the frontend container. If still stale, the image may have been rebuilt without a fresh `pnpm install` — full image rebuild:

```bash
docker compose -p deer-flow-dev -f docker/docker-compose-dev.yaml \
  build --no-cache frontend
docker compose -p deer-flow-dev -f docker/docker-compose-dev.yaml \
  up -d --no-deps --force-recreate frontend
```

### PM2 `nova-tunnel` rapid restart loop

**Root cause**: lock file is unwritable. The wrapper used to write to `/var/run/cloudflared-nova-pm2.lock` (root-owned). Fixed in Phase C0.1: now uses `$XDG_RUNTIME_DIR` (or `/tmp`).

### Gateway returns 401 unexpectedly

**Root cause**: `.env` has `DEER_FLOW_AUTH_DISABLED=0`. To restore dev-mode auth skip, set `DEER_FLOW_AUTH_DISABLED=1` in `.env` and restart the gateway container. Production should keep auth disabled=0.

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

Nova uses Alembic for schema migrations. The migration infrastructure is at `backend/packages/harness/deerflow/persistence/migrations/`.

### When a migration is needed

Any ORM model change that affects the database schema (adding/removing columns, changing types) requires a migration. Never rely on `Base.metadata.create_all()` in production — it only creates tables, not columns.

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

See `docs/MONITORING.md` for observability stack setup and `docs/INSTALL.md` for full onboarding.

---

## 10. Contact / Escalation

- Cloudflare status: https://www.cloudflarestatus.com
- Tunnel documentation: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
- Nova issue tracker: https://github.com/Jahanzaib211/nova/issues

---

## Appendix: Incident Runbooks

### A.1 Disk >90%

**Symptoms**: Alert fires: "Disk usage >90% on mount"
**Metric**: `(1 - node_filesystem_avail_bytes / node_filesystem_size_bytes) * 100 > 90`

**Identify the problem**:
```bash
# See all mounts and usage
df -h

# Largest directories in /home
sudo du -sh /home/* 2>/dev/null | sort -rh | head -10

# Check Docker disk usage
docker system df
```

**Common causes**:
1. **Loki/Prometheus data growth** — the `loki-data` and `prometheus-data` Docker volumes.
   ```bash
   docker exec nova-loki du -sh /loki
   docker exec nova-prometheus du -sh /prometheus
   # Reduce retention if needed (edit loki.yaml / prometheus.yml then restart)
   ```
2. **Docker log overflow** — containers producing excessive logs.
   ```bash
   docker logs --tail 100 <container-name>
   docker update --log-opt max-size=10m --log-opt max-file=3 <container-name>
   ```
3. **Home directory full** — ML model files, datasets, etc.
   ```bash
   du -sh ~/.cache/huggingface 2>/dev/null
   du -sh ~/.llama* 2>/dev/null
   du -sh /home/jahanzaib/Desktop/* 2>/dev/null | sort -rh | head -5
   ```

**Immediate relief**:
```bash
docker system prune -af
sudo apt autoremove -y && sudo apt clean
pm2 flush
python3 /home/jahanzaib/Desktop/nova/scripts/nova-visitors.py --prune 30
```

**Long-term fix**:
1. Add a disk usage alert at 80% as a warning threshold
2. Set up automated cleanup cron (Docker prune weekly, log rotation)
3. Move large datasets to an external mount

---

### A.2 Healthcheck degraded

**Symptoms**: Alert fires: "Healthcheck has non-green probe". Nova Home dashboard shows one or more puppy tiles as **RED** or **YELLOW**.

**What it means**: The `nova-healthcheck` daemon (PM2 `nova-healthcheck`) ran a probe cycle and one or more of its 12 probes returned non-green.

Probes defined in `scripts/healthcheck-daemon.py`:

| Probe | What it checks |
|---|---|
| P1 nginx | HTTP 200 on `http://localhost:2026/health` |
| P2 gateway | HTTP 401 (auth wall) on `http://localhost:2026/api/models` |
| P3 frontend | HTTP 200 with HTML on `http://localhost:2026/` |
| P4 local_llm_gateway | Backend health on `${LOCAL_LLM_GATEWAY_HOST}:${LOCAL_LLM_GATEWAY_PORT}/health` |
| P5 llama_loopback | llama-server at 127.0.0.1:8081/v1/models → 200 |
| P6 llama_vram | 1-token completion test (VRAM ready) |
| P7 docker_count | ≥3 containers running in `deer-flow-dev` project |
| P8 attestation | Binary attestation check (skipped if `WATCHDOG_ATTESTATION_BINARY_PATH` unset) |
| P9 llama_bridge | Bridge reachable at 172.17.0.1:8081 |
| P10 litellm | LiteLLM proxy at 172.17.0.1:4000/v1/models |
| P11 dify | Dify stack initialized (`/console/api/setup` → `step=finished`) |
| P12 tunnel | cloudflared-nova.service active + public URL reachable |

**Immediate fix**:
```bash
# See the last healthcheck cycle output
tail -1 ~/.pm2/logs/nova-healthcheck-out.log | python3 -m json.tool

# Most common: Docker containers dead
pm2 restart deerflow
sleep 90
pm2 logs nova-healthcheck --lines 5

# Or: llama-bridge drifted
pm2 restart llama-bridge
pm2 logs nova-healthcheck --lines 5

# Or: nova-litellm crashed
pm2 restart nova-litellm
pm2 logs nova-healthcheck --lines 5
```

**Auto-fix** (built into daemon):
- P7 docker count < 3 → `pm2 restart deerflow`
- P9 llama-bridge down → `pm2 restart llama-bridge`
- P10 litellm down → `pm2 restart nova-litellm`
- P11 dify down → `pm2 restart nova-dify`
- P12 tunnel down → `sudo systemctl restart cloudflared-nova`

If auto-fix worked, the **next** cycle should return green.

**If it keeps failing**:
```bash
grep -i "error\|timeout\|connrefused\|500\|503" ~/.pm2/logs/nova-healthcheck-out.log | tail -20
python3 scripts/healthcheck-daemon.py --once
```

---

### A.3 Prometheus is down

**Symptoms**: Alert fires: "Prometheus is DOWN — monitoring stack is blind"
**Metric**: `up{job="prometheus"} == 0`

**What it means**: The `nova-prometheus` Docker container has stopped. All metrics scraping ceases. Dashboards will show "No data". Grafana alerting stops evaluating rules. **This is a metadata failure**: the monitored services may still be running fine — Prometheus just can't see them.

**Immediate fix**:
```bash
docker ps -a | grep prometheus
pm2 restart nova-monitoring
# or directly:
docker compose -f docker/monitoring/docker-compose.yaml restart prometheus

# Check logs
docker logs nova-prometheus --tail 30
pm2 logs nova-monitoring --err --lines 50
```

**If the container won't start**:
```bash
docker run --rm -it \
  -v $(pwd)/docker/monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro \
  prom/prometheus:v3.4.1 \
  --check-config
# Common: syntax error in prometheus.yml, targets unreachable
```

**Verify recovery**:
```bash
curl -s http://localhost:9090/-/healthy
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys,json
d=json.load(sys.stdin)
for t in d['data']['activeTargets']:
    print(f'{t[\"labels\"][\"job\"]:20} {t[\"health\"]}')"
# Dashboards repopulate in ~30s
```

**If data directory is corrupted** (loses historical metrics):
```bash
docker volume inspect nova-prometheus_prometheus-data
docker compose -f docker/monitoring/docker-compose.yaml down
docker volume rm nova-prometheus_prometheus-data
make monitoring-up
```

**Prevention**: Use `docker stop` / PM2 restart, not `docker kill`. Check disk space (see Disk >90% runbook).

---

### A.4 Scanner probe spike

**Symptoms**: Alert fires: "Scanner probe spike detected — N hits in 10 minutes"
**Metric**: `count_over_time({job="nova-access", probe="true"}[10m]) > 5`

**What it means**: Someone (or a bot) is systematically probing common vulnerability paths: `/wp-admin`, `/.env`, `/phpmyadmin`, `/.git`, etc. This is **normal** for any public-facing website. The probes are automated (botnets, Shodan, Censys) and not necessarily hostile.

**Response**:
1. Check who is probing in Grafana → **Visitors & Security** dashboard → "Top probe source IPs / 24h". If same IP >100 probe attempts/hour, consider blocking.
2. Optional block at UFW:
   ```bash
   sudo ufw insert 1 deny from <IP> to any comment "scanner probe"
   ```
3. Verify nginx is logging correctly:
   ```bash
   tail -20 /home/jahanzaib/Desktop/nova/logs/nova/access.jsonl | python3 -m json.tool 2>/dev/null | grep '"path"'
   ```

**When to escalate**:
- Coordinated brute-force attack (many IPs, targeted at `/login` or `/api/auth`)
- Suspicious POST requests to `/wp-login.php` or `/xmlrpc.php`
- Requests from residential IPs (not cloud/VPN exit nodes)

**Prevention**: nginx already drops static asset noise, logs with query strings stripped, and uses Cloudflare Access on the tunnel. The alert exists so you know the signal is being monitored.

---

### A.5 Cloudflare tunnel down

**Symptoms**: Alert fires: "Cloudflare tunnel connector is DOWN"
**Metric**: `up{job="cloudflared"} == 0` for >1 minute

**What it means**: The `cloudflared-nova` systemd service has crashed or stopped. Public URLs (`nova.alilabsx.com`, `dash.alilabsx.com`, `status.alilabsx.com`) are unreachable from outside. **LAN access to Nova is unaffected** — users on the same network can still reach `http://localhost:2026` directly.

**Immediate fix**:
```bash
sudo systemctl status cloudflared-nova
sudo systemctl restart cloudflared-nova
sudo systemctl is-active cloudflared-nova
curl -s --max-time 5 https://nova.alilabsx.com/health
```

The PM2 `nova-tunnel` wrapper will also automatically re-nudge systemd if it detects the service is down (two-layer recovery).

**If restart fails**:
```bash
sudo journalctl -u cloudflared-nova -n 50 --no-pager
cat /var/log/cloudflared/nova-error.log | tail -20
```

Common causes:
1. **Token expired**: Cloudflare rotated the tunnel token. Update `/etc/cloudflared/env`.
2. **Port 2026 in use**: Another process grabbed the port.
   ```bash
   ss -tlnp | grep 2026
   ```
3. **DNS misconfigured**: The `nova.alilabsx.com` CNAME no longer points to the tunnel. Verify in Cloudflare DNS settings.

**Persistence**: The tunnel is owned by systemd (not PM2).
```bash
sudo systemctl disable cloudflared-nova  # disable auto-start
sudo systemctl enable cloudflared-nova && sudo systemctl start cloudflared-nova  # re-enable
```