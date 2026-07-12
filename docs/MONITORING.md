# Nova Observability Stack — Operator Guide

## Overview

The Nova observability stack gives you one window into your entire hackathon environment:
visitors, security probes, the 12 puppy barks, tunnel health, host metrics, Docker containers,
and a public status page. Everything is loopback-only; public access is opt-in via the
Cloudflare tunnel + Access.

```
┌─────────────────────────────────────────────────────────────────┐
│  nova.alilabsx.com   → Nova gateway (nginx :2026)              │
│  dash.alilabsx.com   → Grafana :3002 (Cloudflare Access OTP)   │
│  status.alilabsx.com → Uptime Kuma :3003 (Access or public)    │
└─────────────────────────────────────────────────────────────────┘
```

## Components

| Service | Port | Image | What it does |
|---|---|---|---|
| **Grafana** | 3002 | grafana-oss:12.0.1 | Dashboards + alerting + provisioning |
| **Prometheus** | 9090 | prometheus:v3.4.1 | Metrics scraper + TSDB (30d) |
| **Loki** | 3100 | grafana/loki:3.6.12 | Log aggregation (30d) |
| **Alloy** | 12345 | grafana/alloy:v1.9.1 | Log shipper: access.jsonl + healthcheck + journal + docker |
| **node_exporter** | 9100 | prom/node-exporter:v1.9.1 | Host CPU/mem/disk/net |
| **cadvisor** | 9181 | gcr.io/cadvisor/cadvisor:v0.52.1 | Per-container CPU/mem |
| **blackbox_exporter** | 9115 | prom/blackbox-exporter:v0.25.0 | Synthetic HTTP/TCP probes |
| **Uptime Kuma** | 3003 | louislam/uptime-kuma:1.24.0 | Self-hosted status page + external probes |

## Quick Start

```bash
# 1. Create secrets (one-time)
cp docker/monitoring/monitoring.env.example ~/.config/nova/monitoring.env
nano ~/.config/nova/monitoring.env   # set a real GRAFANA_ADMIN_PASSWORD

# 2. Bring up the stack
make monitoring-up

# 3. Verify everything is green
make monitoring-verify

# 4. Open Grafana
open http://localhost:3002
# Login: admin + your password from monitoring.env

# 5. See Nova Home (dashboards auto-loaded)
# Dashboards → Nova → Nova Home
```

## Monitoring URLs

| Service | Local URL | Public URL (after Access setup) |
|---|---|---|
| Grafana | http://localhost:3002 | https://dash.alilabsx.com |
| Prometheus | http://localhost:9090 | — (internal only) |
| Loki | http://localhost:3100 | — (internal only) |
| Uptime Kuma | http://localhost:3003 | https://status.alilabsx.com |

## Adding Public Access

### Grafana — Cloudflare Access (recommended)

1. Cloudflare Zero Trust dashboard → Access → Applications → Add an application
2. Application name: `Nova Grafana`
3. Session duration: 30 days
4. Policy: **Allow** → **Emails** → enter your email
5. For additional security, add **One-time PIN** (Cloudflare sends a code to your email)
6. In the application settings, copy the **AUD tag** and add it to `~/.config/nova/monitoring.env`:
   ```
   CLOUDFLARE_ACCESS_AUD=your_aud_tag_here
   ```
7. Point DNS: `dash.alilabsx.com` → CNAME → `<tunnel>.cfargotunnel.com`
8. Restart the tunnel: `sudo systemctl restart cloudflared-nova`

### Uptime Kuma — Public Status Page

1. Open Uptime Kuma at http://localhost:3003
2. Set a login password on first run
3. Add monitors:
   - **HTTP Monitor**: `https://nova.alilabsx.com/health` (interval: 60s)
   - **HTTP Monitor**: `https://nova.alilabsx.com/api/health` (interval: 60s)
   - **TCP Monitor**: `127.0.0.1:2026` (internal, interval: 30s)
4. Enable the public status page: Settings → Enable status page
5. Point DNS: `status.alilabsx.com` → CNAME → `<tunnel>.cfargotunnel.com`
6. Optional: protect with Cloudflare Access (same steps as Grafana above)

## Dashboards

| Dashboard | UID | What it shows |
|---|---|---|
| **Nova Home** | `nova-home` | 12 puppy tiles + browser/sandbox metrics + tunnel + live visitor tail |
| **Visitors & Security** | `visitors-security` | Scanner probes, 4xx/5xx rate, geomap, live log tail |
| **Host** | `host` | CPU, memory, disk, network, load, uptime |
| **Docker** | `docker` | Container count, restarts, CPU/mem per container |

## Alert Routing

Alerts are managed by Grafana Unified Alerting. Routing:

| Severity | Channel | Contact |
|---|---|---|
| `critical` | ntfy.sh push + Grafana banner | Immediate |
| `warning` | Grafana banner | When you're looking |
| `info` | Dashboard only | Browse |

To enable ntfy push: set `NTFY_TOPIC` in `~/.config/nova/monitoring.env`.
Then install the ntfy app on your phone and subscribe to `your-topic`.

## SLOs

Three SLOs managed by sloth (see `docker/monitoring/sloth/slos.yaml`):

| SLO | Target | Error budget |
|---|---|---|
| Gateway availability | 99.5% | 3.6h/month |
| Healthcheck green | 99.0% | 7.2h/month |
| Tunnel up | 99.0% | 7.2h/month |

To regenerate SLO rules after editing `sloth/slos.yaml`:
```bash
make sloth-generate
curl -X POST http://localhost:9090/-/reload
```

## Grafana Alert Rules

| Alert | Trigger | Severity |
|---|---|---|
| Scanner probe spike | >5 probe hits / 10m | warning |
| HTTP 5xx burst | >0.5 errors/sec for 2m | critical |
| Healthcheck non-green | any probe red/yellow for 2 cycles | warning |
| Cloudflare tunnel down | cloudflared `up==0` for 1m | critical |
| Blackbox probe down | nginx unreachable from host for 2m | warning |
| Disk >90% | any mount | critical |
| Memory >95% | system memory | critical |
| Prometheus down | Prometheus itself not responding | critical |

## Data Retention

| Data | Retention | Location |
|---|---|---|
| Prometheus TSDB | 30 days | `prometheus-data` Docker volume |
| Loki chunks/index | 30 days | `loki-data` Docker volume |
| Visitor log | 30 days | `logs/nova/access.jsonl` (nginx) + Loki |
| Grafana dashboards | Permanent | Git (provisioned) |
| Grafana settings | Permanent | `grafana-data` Docker volume |
| Uptime Kuma DB | Permanent | `uptime-kuma-data` Docker volume |

## Daily Maintenance

```bash
# Prune visitor log (keep 30 days)
python3 scripts/nova-visitors.py --prune 30

# Rotate Loki compactor (runs automatically)

# Check Prometheus metrics storage
docker exec nova-prometheus du -sh /prometheus

# Check Loki storage
docker exec nova-loki du -sh /loki
```

## Restart / Reboot

The stack is managed by PM2 and survives reboots:

```bash
pm2 ls                    # should show nova-monitoring as "online"
pm2 describe nova-monitoring  # see uptime, restarts, logs
pm2 logs nova-monitoring --lines 50  # recent logs

# If the stack won't start after reboot:
pm2 restart nova-monitoring
pm2 logs nova-monitoring --err --lines 50

# Manual restart:
cd /home/jahanzaib/Desktop/nova
make monitoring-down
make monitoring-up
```

## Adding a New Scrape Target

Edit `docker/monitoring/prometheus/prometheus.yml`:

```yaml
- job_name: my-service
  static_configs:
    - targets: ["127.0.0.1:8080"]  # replace with your service
```

Then reload Prometheus: `curl -X POST http://localhost:9090/-/reload`

## Capacity Planning

```
Prometheus:  ~450 series × 4 samples/s × 15s scrape × 30d ≈ 5 GB
Loki:        ~1k streams × 2 GB/day × 30d ≈ 60 GB raw → ~5 GB with compression
Memory caps: prometheus 512m, loki 512m, alloy 256m, cadvisor 256m
```

## Uninstall

```bash
pm2 delete nova-monitoring
make monitoring-down
# Remove DNS entries for dash.alilabsx.com and status.alilabsx.com
# from Cloudflare DNS settings
```
