# ACP agents inside Nova — Claude Code and OpenClaw

Nova's lead agent can hand a task to an external agent over the Agent Client
Protocol with the built-in `invoke_acp_agent` tool. Two agents ship
configured (`acp_agents` in `config.yaml`):

| Agent | What runs | Auth |
|---|---|---|
| `claude_code` | `npx -y @zed-industries/claude-agent-acp@0.23.1` — the Claude Code agent (Claude Agent SDK) behind Zed's ACP adapter | the user's own `claude` login: `~/.claude` mounted **read-only** at `/root/.claude` by `docker/docker-compose.cli-auth.yaml`. Only the Claude Code binary reads it; Nova never loads or forwards that credential. |
| `openclaw` | `node /opt/openclaw/openclaw.mjs acp --url ws://host.docker.internal:18789 --token-file /run/nova/openclaw_token` — OpenClaw's own ACP bridge to the host OpenClaw gateway, reached through `nova-host-bridge` | the gateway token, copied by `scripts/acp-secrets.sh` from `~/.openclaw/openclaw.json` to `~/.nova/secrets/openclaw_token` (0600) and mounted read-only. Never in `config.yaml` or `.env`. |

## Enabling

```bash
echo NOVA_ACP_AGENTS=1 >> .env          # opt-in: mounts ~/.claude (ro) + OpenClaw package/token
pm2 restart nova                         # scripts/pm2-deerflow.sh adds cli-auth + acp overlays
```

`docker/dev-entrypoint.sh` warms the Claude adapter (`npx … --version`) into
the `gateway-npm-cache` volume in the background so the first invocation is
not a download. Settings › Integrations lists both agents (`acp:claude_code`,
`acp:openclaw`) — `healthy` means the command's binary is on the gateway's
`PATH`.

## What the user sees

The agent's text streams into the chat under the "Working with claude_code"
step as it is produced (`acp_update` custom events, contract-pinned), with
tool-call and permission notes in between; the final answer replaces the
stream when the tool returns. Output files land under
`/mnt/acp-workspace/` (per-thread workspace).

## Permissions

The gateway runs as root with the docker socket, so every ACP permission
request is denied unless policy says otherwise:

```yaml
permission_policy:
  allow_kinds: [read, search, fetch, think]   # auto-approved (allow_once)
  deny_kinds: [delete]                        # always denied, even with auto_approve_permissions
```

Kinds are ACP's `ToolKind`: read, edit, delete, move, search, execute, think,
fetch, switch_mode, other. A denied request shows up in the transcript as
`permission denied: execute — <title>`; add the kind to `allow_kinds` to
approve it. `auto_approve_permissions: true` approves everything not in
`deny_kinds` — use it only for agents confined to their workspace.

## Verifying

```bash
# from a chat: "Use the claude_code agent to list the files in its workspace and describe them"
# expect: a Working with claude_code step with streamed text, then the answer.
docker exec deer-flow-gateway sh -c 'ls /root/.claude/.credentials.json && ls /run/nova/openclaw_token && node /opt/openclaw/openclaw.mjs --version'
```
