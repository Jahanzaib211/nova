# Runbook: Healthcheck degraded

## Symptoms
Alert fires: "Healthcheck has non-green probe"
The Nova Home dashboard shows one or more puppy tiles as **RED** or **YELLOW**.

## What it means
The `nova-healthcheck` daemon (PM2 `nova-healthcheck`) ran a probe cycle
and one or more of its 12 probes returned non-green.

The probes are defined in `scripts/healthcheck-daemon.py`. Each probe tests:
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

## Immediate fix

```bash
# See the last healthcheck cycle output
tail -1 ~/.pm2/logs/nova-healthcheck-out.log | python3 -m json.tool

# Which probe is red?
# The JSON will show: "status": "red" or "yellow" for the offending probe

# Most common: Docker containers dead
pm2 restart deerflow
sleep 90  # wait for containers to start
pm2 logs nova-healthcheck --lines 5  # check recovery

# Or: llama-bridge drifted
pm2 restart llama-bridge
pm2 logs nova-healthcheck --lines 5

# Or: nova-litellm crashed
pm2 restart nova-litellm
pm2 logs nova-healthcheck --lines 5
```

## Auto-fix

The `nova-healthcheck` daemon has built-in auto-fixes:
- P7 docker count < 3 → `pm2 restart deerflow`
- P9 llama-bridge down → `pm2 restart llama-bridge`
- P10 litellm down → `pm2 restart nova-litellm`
- P11 dify down → `pm2 restart nova-dify`
- P12 tunnel down → `sudo systemctl restart cloudflared-nova`

If auto-fix worked, the **next** cycle should return green.

## If it keeps failing

```bash
# Check the error log for the specific failing probe
grep -i "error\|timeout\|connrefused\|500\|503" ~/.pm2/logs/nova-healthcheck-out.log | tail -20

# Force a manual healthcheck cycle
python3 scripts/healthcheck-daemon.py --once
```
