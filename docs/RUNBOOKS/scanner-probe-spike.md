# Runbook: Scanner probe spike

## Symptoms
Alert fires: "Scanner probe spike detected — N hits in 10 minutes"
Metric: `count_over_time({job="nova-access", probe="true"}[10m]) > 5`

## What it means
Someone (or a bot) is systematically probing common vulnerability paths:
`/wp-admin`, `/.env`, `/phpmyadmin`, `/.git`, etc.

This is **normal** for any public-facing website. The probes are:
- Automated (botnets, scanners like Shodan, Censys)
- Not necessarily hostile — most are routine security research

## Response

### 1. Check who is probing (no action required, just for awareness)

Open Grafana → **Visitors & Security** dashboard.
Look at the "Top probe source IPs / 24h" table.
If the same IP is making >100 probe attempts/hour, consider blocking it.

### 2. Optional: Block the IP at UFW

```bash
# Get the offending IP from the Visitors dashboard table
# Replace <IP> with the address
sudo ufw insert 1 deny from <IP> to any comment "scanner probe"
```

### 3. Verify nginx is logging correctly

```bash
# Check access.jsonl for recent probes
tail -20 /home/jahanzaib/Desktop/nova/logs/nova/access.jsonl | python3 -m json.tool 2>/dev/null | grep '"path"'
```

## When to escalate

If you see:
- A coordinated brute-force attack (many IPs, targeted at `/login` or `/api/auth`)
- Suspicious POST requests to `/wp-login.php` or `/xmlrpc.php`
- Requests from IPs that appear to be residential (not cloud/VPN exit nodes)

In these cases, consider enabling fail2ban or a cloud provider WAF rule.

## Prevention

The nginx configuration already:
- Drops static asset noise
- Logs with query strings stripped (tokens never hit disk)
- Uses Cloudflare Access on the tunnel

There is nothing to "fix" here — this is expected background noise.
The alert exists so you know the signal is being monitored.
