# Integrations registry

What Nova can reach from this machine, and whether it works — one page in
Settings › Integrations, one API, one health vocabulary shared with the ops
console (`contracts/integrations_health_contract.json`).

## Sources

| Source | Where it comes from | Probe |
|---|---|---|
| Services | `integrations.services` in `config.yaml` (hot-reloaded) | HTTP GET against the service's real health endpoint, 3 s timeout, all in parallel |
| MCP servers | `extensions_config.json` | tool count in the gateway's MCP cache — never a live connect on the request path |
| Skills | skill storage | loaded / disabled |
| ACP agents | `acp_agents` in `config.yaml` | the command's binary is on `PATH` |

Service ids `ollama`, `litellm`, `mailcow`, `chatwoot`, `twenty`, `openclaw`,
`searxng`, `crawl4ai`, `browserless` get a service-aware probe (model lists,
Mailcow version, key-not-set detection). Any other id is a plain GET against
`health_path`.

Secrets are named, not inlined: `api_key_env: MAILCOW_API_KEY` is the name of
an environment variable read when the probe runs. An unset variable makes the
card say so (`degraded`); a `$VAR` value in config.yaml would instead fail the
whole config load, which is the wrong failure mode for an optional integration.

## Statuses

`healthy` · `degraded` (reachable but not fully usable: auth missing, zero
tools loaded) · `down` (refused / timed out / 5xx) · `unknown` (probe itself
failed, or the MCP cache has not loaded yet) · `disabled` (switched off in
config).

## API

```
GET  /api/integrations            # {enabled, probe_cache_seconds, integrations[]}
GET  /api/integrations?refresh=1  # bypass the cache
GET  /api/integrations/{id}
POST /api/integrations/{id}/probe # probe one now
```

Session auth (`@require_auth`). Results are cached for
`integrations.probe_cache_seconds` (default 20 s).

## Reaching loopback-only host services

Chatwoot (:4800), Twenty (:3008), the Mailcow admin API (:8080) and OpenClaw
(:18789) bind `127.0.0.1` on the host, so `host.docker.internal:<port>` is
refused from a container. The `nova-host-bridge` PM2 app
(`scripts/host-bridge.sh`) forwards those ports from the docker bridge IP
(`172.17.0.1`) to loopback — nothing on the LAN can reach them, and every
upstream still enforces its own API key. Watchdog probe `P16_host_bridge`
checks the forwarders and heals the app via pm2.

```bash
pm2 start ecosystem.config.js --only nova-host-bridge && pm2 save
```

## Adding a service

1. Add it under `integrations.services` (see `config.example.yaml`).
2. If it needs a smarter probe than "GET health_path is 200", add a client in
   `deerflow/integrations/clients.py` and register it in `CLIENTS`.
3. Tests: `tests/test_integrations_registry.py` (httpx `MockTransport`).
