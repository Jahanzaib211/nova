# Visitor Logging — what Nova records, and why

Nova keeps a **first-party access log** of requests to `nova.alilabsx.com` for
security and abuse prevention: knowing who reaches the site, spotting scanners,
and investigating incidents. This is standard server-side logging of our own
service — not third-party tracking, ad profiling, or scanning anyone's machine.

## What is recorded

Per request, one JSON line in `logs/nova/access.jsonl` (nginx `log_format
visitors`, see [`docker/nginx/nginx.conf`](../docker/nginx/nginx.conf)):

| Field | Example | Purpose |
|---|---|---|
| `ts` | `2026-07-11T00:12:03+00:00` | when |
| `ip` | real visitor IP (from Cloudflare `CF-Connecting-IP`) | attribution / rate-limit key |
| `country` | `PK` (Cloudflare `CF-IPCountry`) | geo context, no lookup DB needed |
| `method` `path` | `GET /workspace/chats/new` | what was requested (**query string dropped**) |
| `status` `bytes` | `200` `11701` | outcome |
| `referer` `ua` | where from / which browser | bot vs human, traffic source |
| `cf_ray` | Cloudflare edge id | correlate with Cloudflare logs |

## What is deliberately NOT recorded

- **No query strings** — the log stores `$uri` (path only), so tokens, reset
  codes, or search terms that ride in `?...=` never touch disk.
- **No cookies, no `Authorization` header, no request/response bodies.**
- **No passwords or form contents** — nginx logs the request line, not payloads.
- Static assets, HMR, and internal health probes are filtered out (`$loggable`)
  so the store is about real visitors, not noise.

## Retention & access

- **30-day retention.** Prune with:
  ```bash
  python3 scripts/nova-visitors.py --prune 30
  ```
  Wire this into a daily cron / the healthcheck daemon to enforce it.
- The log lives on the host at `logs/nova/access.jsonl` (git-ignored, never
  committed). Access is limited to the server operator.

## How to read it

```bash
python3 scripts/nova-visitors.py                 # last 24h: who, where, top paths, security
python3 scripts/nova-visitors.py --since 7d      # last week
python3 scripts/nova-visitors.py --recent 40     # latest real visits
python3 scripts/nova-visitors.py --security      # scanners, 4xx bursts, auth failures
python3 scripts/nova-visitors.py --ip 203.0.113.7  # one visitor's full timeline
```

## Lawful basis / ethics

Processing real IPs for **security and abuse prevention** is a recognised
legitimate interest (GDPR Art. 6(1)(f); an IP is personal data). We keep the
minimum needed, bound retention, drop sensitive query data, and don't share it.
If you publish a privacy policy for the site, add one line: *"We keep short-term,
minimised server access logs (IP, country, requested page, timestamp) for
security and abuse prevention, retained for 30 days."*

## Note on integrity

`set_real_ip_from` trusts only the tunnel's local ingress, so a direct LAN hit
to `:2026` cannot spoof `CF-Connecting-IP`. All public traffic arrives through
Cloudflare, where the header is authoritative.
