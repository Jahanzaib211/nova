# Cloudflare Tunnel — Nova Hackathon

Enterprise-grade Cloudflare Tunnel setup for `nova.alilabsx.com` → Nova
gateway on `localhost:2026`. Reboot-proof, auto-restarting, logrotated,
watchdog-monitored.

## Architecture

```
                   internet
                      │
                      ▼
        ┌──────────────────────────────┐
        │   Cloudflare Edge (anycast)  │  ← HTTPS terminates here
        │   104.21.x.x / 172.67.x.x    │
        └──────────────┬───────────────┘
                       │ QUIC (encrypted tunnel)
                       ▼
   ┌────────────────────────────────────────┐
   │ systemd: cloudflared-nova.service      │ ← auto-restarts on crash
   │ /etc/cloudflared/config.yml            │
   │ /etc/cloudflared/env (token, mode 0600)│
   │                                        │
   │ systemd-managed, runs as root          │
   │ Hardened: NoNewPrivileges, PrivateTmp, │
   │ ProtectSystem=strict, etc.             │
   └──────────────┬─────────────────────────┘
                  │ HTTP (plain, localhost only)
                  ▼
        ┌──────────────────────────────┐
        │  nginx :2026 (deer-flow-dev) │
        │  → gateway :8001 (docker)    │
        └──────────────────────────────┘

   PM2 layer (observability + auto-fix):
     nova-tunnel        — tails journald, emits heartbeat every 30s
     nova-healthcheck   — P12_tunnel probe + auto-fix
                          (`sudo systemctl restart cloudflared-nova`)
```

## Files

| Path | Purpose |
| --- | --- |
| `config/cloudflared-nova.yml` | Tunnel ingress rules (canonical) |
| `config/cloudflared-nova.service` | systemd unit (canonical) |
| `config/cloudflared-nova.env` | Token template (canonical) |
| `config/cloudflared-nova.logrotate` | logrotate config |
| `scripts/install-cloudflared-nova.sh` | One-shot installer |
| `scripts/pm2-cloudflared-nova.sh` | PM2 wrapper (heartbeat + journal tail) |
| `ecosystem.config.js` | PM2 app `nova-tunnel` (added in this PR) |
| `scripts/healthcheck-daemon.py` | Watchdog with P12_tunnel probe + auto-fix |

## Boot sequence

1. **systemd** brings up `cloudflared-nova.service` (Type=simple, Restart=always,
   RestartSec=5s, StartLimitBurst=10 in 300s).
2. **PM2** daemon (`pm2 startup` already configured) starts `nova-tunnel`,
   which:
   - calls `sudo systemctl start cloudflared-nova.service` (idempotent nudge)
   - tails `journalctl -u cloudflared-nova` to `/var/log/cloudflared/nova-{out,error}.log`
   - emits a 30s heartbeat to `/var/log/cloudflared/nova-heartbeat.log`
3. **PM2** also starts `nova-healthcheck`, which polls every 30s:
   - `P12_tunnel` checks systemd active + public URL HTTP 200
   - 2 consecutive REDs → `sudo systemctl restart cloudflared-nova.service`
     (NOPASSWD via `/etc/sudoers.d/nova-watchdog`)
4. **logrotate** daily prunes `/var/log/cloudflared/nova-*.log` (14d retention),
   sending SIGHUP to cloudflared to reopen log files without dropping tunnels.

## Install

```bash
TUNNEL_TOKEN=eyJhIjoi...   # from Cloudflare dashboard
sudo -v                    # cache password for the run
./scripts/install-cloudflared-nova.sh
```

Re-running the installer is safe — it overwrites configs idempotently and
restarts the systemd unit + PM2 process at the end.

## Operating

```bash
# Check public reachability
curl https://nova.alilabsx.com/health

# Service state
sudo systemctl status cloudflared-nova
journalctl -u cloudflared-nova -f

# PM2 observability
pm2 logs nova-tunnel
pm2 logs nova-healthcheck --err   # P12_tunnel RED → auto-fix issued

# Tunnel metrics (Prometheus format)
curl 127.0.0.1:20001/metrics

# Manual restart (e.g. after changing the dashboard route)
sudo systemctl restart cloudflared-nova
pm2 restart nova-tunnel
```

## Rotating the tunnel token

1. Cloudflare dashboard → Zero Trust → Networks → Tunnels → Nova hackathon
   → Configure → **Regenerate token**.
2. Update `/etc/cloudflared/env` with the new token (mode 0600).
3. `sudo systemctl restart cloudflared-nova`.

The PM2 wrapper picks up the new token automatically on the next restart
(it doesn't read the token — systemd does).

## Adding a new public hostname

Add the route in the Cloudflare dashboard (Zero Trust → Tunnels → Nova
hackathon → Configure → Public Hostname). The dashboard config is pushed
down to cloudflared on the next tunnel reconnect — no local file changes
needed.

The local `/etc/cloudflared/config.yml` only matters if you want to bypass
the dashboard (e.g. for emergency ingress rules). For normal operations,
the dashboard is the source of truth.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| P12_tunnel RED, `systemd=inactive` | systemd unit crashed | Auto-fix restarts it; or `sudo systemctl start cloudflared-nova` |
| P12_tunnel RED, `edge=ConnectError` | DNS or proxy misconfigured | Check `dig nova.alilabsx.com A +short` → should show Cloudflare anycast IPs |
| `404 Not Found` from public URL | Hostname not in tunnel ingress | Add it via dashboard |
| `502 Bad Gateway` from public URL | Origin (nginx:2026) is down | Check `curl http://localhost:2026/health` |
| Sudo password prompts from watchdog | NOPASSWD rule missing | Re-run installer; check `/etc/sudoers.d/nova-watchdog` |

## Why systemd AND PM2?

- **systemd** owns the lifecycle (boot, crash, OOM-kill recovery). It's
  the only thing that runs as root, so it can survive PM2 daemon crashes.
- **PM2** owns observability (heartbeat, journal tailing, exit codes)
  and integration with the watchdog auto-fix pipeline. PM2 alone isn't
  reboot-proof without `pm2 startup`, which we have, but systemd is more
  battle-tested for "must be up after reboot" semantics.
- Both layers independently auto-recover. If PM2 dies, systemd keeps the
  tunnel alive (PM2 just stops tailing). If systemd dies, PM2 restarts it.
