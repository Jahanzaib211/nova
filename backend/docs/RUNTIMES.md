# Runtimes — Claude Code and OpenClaw as chat runtimes

A *runtime* is what runs a chat turn. `native` is Nova's LangGraph lead
agent. Every `acp_agents` entry (`claude_code`, `openclaw`) is a runtime
too, reached over the Agent Client Protocol — the same idea as OpenClaw's
`agentRuntime.id`, where the Claude CLI backend powers whole conversations
rather than being a tool the built-in agent calls.

## Selection

Precedence, highest first (`deerflow.runtimes.RuntimeRegistry.select`):

1. **Chat** — the run context keys `runtime`, `runtime_account`,
   `permission_mode` (the model picker's "Account for this chat" and the
   permission chip). `runtime: native` is the explicit reset.
2. **Model** — `runtime: claude_code` on a `models:` entry in `config.yaml`
   routes every chat on that model to Claude Code.
3. **Default** — `runtimes.default` (`native`).

Unknown values degrade to the next level; nothing raises mid-run.

## How a turn runs

`RuntimeDispatchMiddleware` (registered in `build_middlewares` when
`runtimes.enabled`) wraps the model call. For `native` it is a no-op. For an
ACP runtime it:

1. Mints an **ephemeral harness token** for the signed-in user and mounts
   Nova's own MCP server (`runtimes.nova_mcp_url`) in the ACP session, plus
   Nova's enabled MCP client servers — so Claude Code can call `jobs__list`,
   `integrations__probe`, `agents__delegate`, … as that user. The token is
   revoked when the turn ends.
2. Turns the conversation into one prompt (`transcript_for_prompt`: the
   last six exchanges as text, then the request).
3. Runs it through `deerflow.runtimes.acp_transport.run_acp_prompt` (shared
   with the `invoke_acp_agent` tool) in the thread's ACP workspace, with the
   permission preset below.
4. Streams every text chunk and tool/permission decision as `acp_update`
   custom events (contract-pinned) — the UI renders them under "Working with
   claude_code" exactly as for the tool.
5. Returns the answer as the turn's `AIMessage` (`response_metadata.runtime`
   set). Threads, checkpoints, titles, memory and summarization see an
   ordinary turn. A transport failure becomes a message, not an exception.

## Permission modes

| Mode | ACP policy | Chip |
|---|---|---|
| `full` | auto-approve everything except `delete` | Default (Full Access) |
| `standard` | allow `read, search, fetch, think`; edits and commands denied and shown in the transcript | Standard |
| `plan` | as standard, and `edit, delete, move, execute` explicitly denied | Plan (read-only) |

`policy_for_mode()` is the single mapping; the `invoke_acp_agent` tool keeps
using the agent's own `permission_policy` for delegated subtasks.

## Accounts

Presence-checked, never read: `claude-login` (`<claude_login_dir>/.credentials.json`,
`/root/.claude` when `docker-compose.cli-auth.yaml` mounts your `~/.claude`
read-only), `anthropic-api-key` (`ANTHROPIC_API_KEY` set), `gateway-token`
(`/run/nova/openclaw_token`). `auto` picks the first available.

## Operations (capability module `runtimes`, flag `runtimes`)

- `runtimes.list` — runtimes × accounts × availability, default, modes.
- `runtimes.probe` — "Check model": one-word round trip through the runtime,
  with latency or the exact error. Verified 2026-09-20 on the host:
  `claude_code` / `claude-login` → `ok` in 18.7 s (adapter cold start).
- `runtimes.sessions` — Claude Code sessions under `<login dir>/projects/`,
  grouped by project (names and timestamps only).

## Enabling

```yaml
runtimes:
  enabled: true          # config.yaml; hot-reloads
  default: native
  nova_mcp_url: http://127.0.0.1:8001/api/mcp/nova
```

plus `NOVA_ACP_AGENTS=1` in `.env` and `pm2 restart nova` so the
`cli-auth` and `acp` overlays mount the login and the OpenClaw token
(`backend/docs/ACP_AGENTS.md`).
