# Runbook: Cloudflare tunnel down

## Symptoms
Alert fires: "Cloudflare tunnel connector is DOWN"
Metric: `up{job="cloudflared"} == 0` for >1 minute

## What it means
The `cloudflared-nova` systemd service has crashed or stopped.
Public URLs (`nova.alilabsx.com`, `dash.alilabsx.com`, `status.alilabsx.com`)
are unreachable from outside your network.

**LAN access to Nova is unaffected.** Users on the same network can still reach
`http://localhost:2026` directly.

## Immediate fix

```bash
# Check the systemd unit status
sudo systemctl status cloudflared-nova

# Restart the tunnel
sudo systemctl restart cloudflared-nova

# Verify it's up
sudo systemctl is-active cloudflared-nova
curl -s --max-time 5 https://nova.alilabsx.com/health
```

The PM2 `nova-tunnel` wrapper will also automatically re-nudge systemd if it detects
the service is down (this is a two-layer recovery system).

## If restart fails

Check the logs:
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
3. **DNS misconfigured**: The `nova.alilabsx.com` CNAME no longer points to the tunnel.
   Go to Cloudflare DNS settings and verify the CNAME.

## Persistence

The tunnel is owned by systemd (not PM2). To disable auto-start:
```bash
sudo systemctl disable cloudflared-nova
```

To re-enable:
```bash
sudo systemctl enable cloudflared-nova
sudo systemctl start cloudflared-nova
```
