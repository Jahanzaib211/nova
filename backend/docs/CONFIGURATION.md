# Configuration Guide

This guide explains how to configure Nova for your environment.

## Config Versioning

`config.example.yaml` contains a `config_version` field that tracks schema changes. When the example version is higher than your local `config.yaml`, the application emits a startup warning:

```
WARNING - Your config.yaml (version 0) is outdated — the latest version is 1.
Run `make config-upgrade` to merge new fields into your config.
```

- **Missing `config_version`** in your config is treated as version 0.
- Run `make config-upgrade` to auto-merge missing fields (your existing values are preserved, a `.bak` backup is created).
- When changing the config schema, bump `config_version` in `config.example.yaml`.

## Configuration Sections

### Models

Configure the LLM models available to the agent:

```yaml
models:
  - name: gpt-4o                    # Internal identifier
    display_name: GPT-4o            # Human-readable name
    use: langchain_openai:ChatOpenAI  # LangChain class path
    model: gpt-4o                   # Model identifier for API
    api_key: $OPENAI_API_KEY        # API key (use env var)
    max_tokens: 4096               # Max tokens per request
    temperature: 0.7               # Sampling temperature
```

**Supported Providers**:

- OpenAI (`langchain_openai:ChatOpenAI`)
- Anthropic (`langchain_anthropic:ChatAnthropic`)
- DeepSeek (`langchain_deepseek:ChatDeepSeek`)
- Xiaomi MiMo (`deerflow.models.patched_mimo:PatchedChatMiMo`)
- Claude Code OAuth (`deerflow.models.claude_provider:ClaudeChatModel`)
- Codex CLI (`deerflow.models.openai_codex_provider:CodexChatModel`)
- Any LangChain-compatible provider

CLI-backed provider examples:

```yaml
models:
  - name: gpt-5.4
    display_name: GPT-5.4 (Codex CLI)
    use: deerflow.models.openai_codex_provider:CodexChatModel
    model: gpt-5.4
    supports_thinking: true
    supports_reasoning_effort: true

  - name: claude-sonnet-4.6
    display_name: Claude Sonnet 4.6 (Claude Code OAuth)
    use: deerflow.models.claude_provider:ClaudeChatModel
    model: claude-sonnet-4-6
    max_tokens: 4096
    supports_thinking: true
```

**Auth behavior for CLI-backed providers**:

- `CodexChatModel` loads Codex CLI auth from `~/.codex/auth.json`
- The Codex Responses endpoint currently rejects `max_tokens` and `max_output_tokens`, so `CodexChatModel` does not expose a request-level token cap
- `ClaudeChatModel` accepts `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR`, `CLAUDE_CODE_CREDENTIALS_PATH`, or plaintext `~/.claude/.credentials.json`
- On macOS, Nova does not probe Keychain automatically. Use `scripts/export_claude_code_oauth.py` to export Claude Code auth explicitly when needed

To use OpenAI's `/v1/responses` endpoint with LangChain, keep using `langchain_openai:ChatOpenAI` and set `use_responses_api: true` with `output_version: responses/v1`.

For OpenAI-compatible gateways (for example Novita or OpenRouter), keep using `langchain_openai:ChatOpenAI` and set `base_url`:

```yaml
models:
  - name: novita-deepseek-v3.2
    display_name: Novita DeepSeek V3.2
    use: langchain_openai:ChatOpenAI
    model: deepseek/deepseek-v3.2
    api_key: $NOVITA_API_KEY
    base_url: https://api.novita.ai/openai
    supports_thinking: true
    when_thinking_enabled:
      extra_body:
        thinking:
          type: enabled

  - name: minimax-m3
    display_name: MiniMax M3
    use: langchain_openai:ChatOpenAI
    model: MiniMax-M3
    api_key: $MINIMAX_API_KEY
    base_url: https://api.minimax.io/v1
    max_tokens: 4096
    temperature: 1.0  # MiniMax requires temperature in (0.0, 1.0]
    supports_vision: true

  - name: minimax-m2.7
    display_name: MiniMax M2.7
    use: langchain_openai:ChatOpenAI
    model: MiniMax-M2.7
    api_key: $MINIMAX_API_KEY
    base_url: https://api.minimax.io/v1
    max_tokens: 4096
    temperature: 1.0  # MiniMax requires temperature in (0.0, 1.0]
    supports_vision: false  # M2.7 is text-only; M3 supports vision

  - name: minimax-m2.7-highspeed
    display_name: MiniMax M2.7 Highspeed
    use: langchain_openai:ChatOpenAI
    model: MiniMax-M2.7-highspeed
    api_key: $MINIMAX_API_KEY
    base_url: https://api.minimax.io/v1
    max_tokens: 4096
    temperature: 1.0  # MiniMax requires temperature in (0.0, 1.0]
    supports_vision: false  # M2.7 is text-only; M3 supports vision
```

If your OpenRouter key lives in a different environment variable name, point `api_key` at that variable explicitly (for example `api_key: $OPENROUTER_API_KEY`).

To route OpenAI models through `/v1/responses`, keep using `langchain_openai:ChatOpenAI` and set `use_responses_api: true` with `output_version: responses/v1`.

For vLLM 0.19.0, use `deerflow.models.vllm_provider:VllmChatModel`; for Qwen-style parsers prefer `when_thinking_enabled.extra_body.chat_template_kwargs.enable_thinking`, and Nova will also normalize the older `thinking` alias.

### Tool Groups

Organize tools into logical groupings:

```yaml
tool_groups:
  - name: web          # Web browsing and search
  - name: file:read    # Read-only file operations
  - name: file:write   # Write file operations
  - name: bash         # Shell command execution
```

### Tools

Configure specific tools available to the agent:

```yaml
tools:
  - name: web_search
    group: web
    use: deerflow.community.tavily.tools:web_search_tool
    max_results: 5
    # api_key: $TAVILY_API_KEY  # Optional
```

**Built-in Tools**:

- `web_search` - Search the web (DuckDuckGo, Tavily, Brave, Exa, InfoQuest, Firecrawl)
- `web_fetch` - Fetch web pages (Jina AI, Exa, InfoQuest, Firecrawl)
- `image_search` - Search for reference images (DuckDuckGo, InfoQuest, Serper)
- `ls` - Directory listing (tree format, max 2 levels)
- `read_file` - Read file contents with optional line range
- `write_file` - Write/append to files, creates directories; overwrites by default and exposes the `append` argument in the model-facing schema for end-of-file writes
- `str_replace` - Substring replacement (single or all occurrences); same-path serialization is scoped to `(sandbox.id, path)` so isolated sandboxes do not contend on identical virtual paths inside one process

### Sandbox

Nova supports multiple sandbox execution modes. Configure your preferred mode in `config.yaml`:

**Local Execution** (runs sandbox code directly on the host machine):

```yaml
sandbox:
   use: deerflow.sandbox.local:LocalSandboxProvider # Local execution
   allow_host_bash: false # default; host bash is disabled unless explicitly re-enabled
```

**Docker Execution** (runs sandbox code in isolated Docker containers):

```yaml
sandbox:
   use: deerflow.community.aio_sandbox:AioSandboxProvider # Docker-based sandbox
```

**Docker Execution with Kubernetes** (runs sandbox code in Kubernetes pods via provisioner service):

This mode runs each sandbox in an isolated Kubernetes Pod on your **host machine's cluster**. Requires Docker Desktop K8s, OrbStack, or similar local K8s setup.

```yaml
sandbox:
   use: deerflow.community.aio_sandbox:AioSandboxProvider
   provisioner_url: http://provisioner:8002
```

When using Docker development (`make docker-start`), DeerFlow starts the `provisioner` service only when this provisioner mode is configured. In Local/Docker modes, `provisioner` is not started.

See the [Sandbox Configuration Guide](backend/docs/CONFIGURATION.md#sandbox) to configure your preferred mode.

**Custom sandbox images**: the Docker-based modes above default to a
pre-built image (`sandbox.image` in `config.yaml`, `enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest`)
pulled by tag — there is no local Dockerfile for it. To add tooling the
default image lacks (for example an Android build toolchain — JDK, Android
SDK, Gradle, Kotlin), extend it with your own Dockerfile and point
`sandbox.image` at the result. See [`docker/sandbox/README.md`](../../docker/sandbox/README.md)
for a worked example (`Dockerfile.android`).

### Virtual Path System

| Virtual Path | Physical Path |
|-------------|---------------|
| `/mnt/user-data/{workspace,uploads,outputs}` | `backend/.nova/users/{user_id}/threads/{thread_id}/user-data/...` |
| `/mnt/skills` | `deer-flow/skills/` directory |

### Skills

Configure the skills directory for specialized workflows:

```yaml
skills:
  # Host path (optional, default: ../skills)
  path: /custom/path/to/skills

  # Container mount path (default: /mnt/skills)
  container_path: /mnt/skills
```

**How Skills Work**:

- Skills are stored in `deer-flow/skills/{public,custom}/`
- Each skill has a `SKILL.md` file with metadata
- Skills are automatically discovered and loaded
- Available in both local and Docker sandbox via path mapping

**Per-Agent Skill Filtering**:
Custom agents can restrict which skills they load by defining a `skills` field in their `config.yaml` (located at `workspace/agents/<agent_name>/config.yaml`):

- **Omitted or `null`**: Loads all globally enabled skills (default fallback).
- **`[]` (empty list)**: Disables all skills for this specific agent.
- **`["skill-name"]`**: Loads only the explicitly specified skills.

### Title Generation

Automatic conversation title generation:

```yaml
title:
  enabled: true
  max_words: 6
  max_chars: 60
  model_name: null  # Use first model in list
```

### GitHub API Token (Optional for GitHub Deep Research Skill)

The default GitHub API rate limits are quite restrictive. For frequent project research, we recommend configuring a personal access token (PAT) with read-only permissions.

**Configuration Steps**:

1. Uncomment the `GITHUB_TOKEN` line in the `.env` file and add your personal access token
2. Restart the DeerFlow service to apply changes

### LangSmith Tracing

Nova has built-in [LangSmith](https://smith.langchain.com) integration for observability. When enabled, all LLM calls, agent runs, tool executions, and middleware processing are traced and visible in the LangSmith dashboard.

**Setup:**

1. Sign up at [smith.langchain.com](https://smith.langchain.com) and create a project.
2. Add the following to your `.env` file in the project root:

```bash
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxx
LANGSMITH_PROJECT=xxx
```

**Legacy variables:** The `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, and `LANGCHAIN_ENDPOINT` variables are also supported for backward compatibility. `LANGSMITH_*` variables take precedence when both are set.

### Langfuse Tracing

Nova also supports [Langfuse](https://langfuse.com) observability for LangChain-compatible runs.

Add the following to your `.env` file:

```bash
LANGFUSE_TRACING=true
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

If you are using a self-hosted Langfuse deployment, set `LANGFUSE_BASE_URL` to your Langfuse host.

### Dual Provider Behavior

If both LangSmith and Langfuse are enabled, Nova initializes and attaches both callbacks so the same run data is reported to both systems.

If a provider is explicitly enabled but required credentials are missing, or the provider callback cannot be initialized, Nova raises an error when tracing is initialized during model creation instead of silently disabling tracing.

**Docker:** In `docker-compose.yaml`, tracing is disabled by default (`LANGSMITH_TRACING=false`). Set `LANGSMITH_TRACING=true` and/or `LANGFUSE_TRACING=true` in your `.env`, together with the required credentials, to enable tracing in containerized deployments.

---

## Configuration Location

The configuration file should be placed in the **project root directory** (`nova/config.yaml`). Set `NOVA_PROJECT_ROOT` when the process may start from another working directory, or set `NOVA_CONFIG_PATH` to point at a specific file.

## Configuration Priority

DeerFlow searches for configuration in this order:

1. Explicit `config_path` argument
2. `NOVA_CONFIG_PATH` environment variable
3. `config.yaml` under `NOVA_PROJECT_ROOT`, or under the current working directory when `NOVA_PROJECT_ROOT` is unset
4. `config.yaml` in parent directory (project root - **recommended location**)

Config values starting with `$` are resolved as environment variables (e.g., `$OPENAI_API_KEY`).
`ModelConfig` also declares `use_responses_api` and `output_version` so OpenAI `/v1/responses` can be enabled explicitly while still using `langchain_openai:ChatOpenAI`.

### Extensions Configuration (`extensions_config.json`)

MCP servers and skill states in a single file:

Configuration priority:

1. Explicit `config_path` argument
2. `NOVA_EXTENSIONS_CONFIG_PATH` environment variable
3. `extensions_config.json` in current directory (backend/)
4. `extensions_config.json` in parent directory (project root - **recommended location**)

Both can be modified at runtime via Gateway API endpoints or `NovaClient` methods.

---

## Security Notes

### Sandbox Isolation and the Docker Socket (DooD)

Nova executes agent-generated shell/code through a configurable sandbox
(`sandbox.use` in `config.yaml`). The isolation guarantees differ by mode, and
one mode requires mounting the host Docker socket. Understand the trade-offs
before exposing an instance to untrusted input.

| Mode | `config.yaml` | Host Docker socket | Isolation |
|------|---------------|--------------------|-----------|
| `local` (default) | `deerflow.sandbox.local:LocalSandboxProvider` | Not mounted | Commands run **inside the gateway container** on its filesystem. Not a strong boundary — `allow_host_bash` is `false` by default and should stay off for untrusted workloads. |
| `aio` (pure DooD) | `deerflow.community.aio_sandbox:AioSandboxProvider` (no `provisioner_url`) | **Mounted** (opt-in overlay) | Sandbox containers are started via the host Docker daemon. |
| `provisioner` (Kubernetes) | `AioSandboxProvider` + `provisioner_url` | Not mounted | Sandbox pods are created through the provisioner's K8s API over HTTP. Strongest isolation. |

#### The Docker socket is host root

Mounting `/var/run/docker.sock` into a container grants that container
**root-equivalent control of the host**: anything able to reach the socket can
start a new container that bind-mounts the host filesystem and escape. This
matters for Nova because the gateway executes model-generated commands, so a
prompt injection or any in-container code-execution primitive could pivot to the
host through the socket.

To keep this off the default attack surface:

- The host Docker socket is **not** mounted by the default Compose stack. It is
  added only for `aio` mode through the opt-in `docker/docker-compose.dood.yaml`
  overlay, which `scripts/deploy.sh` and `scripts/docker.sh` append
  automatically when `detect_sandbox_mode()` returns `aio`.
- Prefer **provisioner/Kubernetes mode** for multi-tenant or internet-exposed
  deployments — it isolates sandboxes without handing the gateway the host
  daemon.
- If you must use `aio`/DooD, treat the host as part of the gateway's trust
  boundary: run it on a dedicated host, and consider a scoped Docker API proxy
  instead of the raw socket.

> Note: the gateway bind-mounts `$HOME/.claude` and `$HOME/.codex` (read-only)
> for CLI auto-auth in **all** modes. These hold long-lived CLI credentials;
> scope or omit them when the gateway runs untrusted workloads.

### CLI Credential Mounts (Claude Code / Codex)

Nova can reuse your Claude Code / Codex CLI subscription login as a model
provider (`ClaudeChatModel`, the Codex provider) or for ACP agents that run the
CLI in-container. The Compose stack used to bind-mount the **entire** `~/.claude`
and `~/.codex` directories (read-only) into the gateway container in **every**
configuration — exposing not just credentials but full conversation history,
per-project session data, and global CLI config. A gateway compromise (prompt
injection, tool/MCP misuse, RCE) would leak all of it.

These directories are **no longer mounted by default**. Supply CLI credentials
with the least exposure that fits your setup:

| Need | How | Exposure |
|------|-----|----------|
| Claude model provider | env `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_AUTH_TOKEN` (via `.env`), or `CLAUDE_CODE_CREDENTIALS_PATH` → a single mounted `.credentials.json` | none / one file |
| Codex model provider | env `CODEX_AUTH_PATH` pointing at a single mounted `auth.json` | one file |
| ACP agent | the adapter's own auth — many ACP adapters take an env API key (e.g. `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) and need no mount; use the opt-in `docker/docker-compose.cli-auth.yaml` overlay only if your adapter reads the full CLI config dir | none / full dir |

The Gateway credential loader checks environment variables **before** the
default credential files, so the env-token paths need no bind mount at all. ACP
adapters authenticate independently of Nova via their own documented env —
for example the common `claude-code-acp` adapter starts as
`ANTHROPIC_API_KEY=… claude-code-acp` and honors `CLAUDE_CONFIG_DIR` to redirect
its config directory, so it needs no `~/.claude` mount at all. Prefer the
adapter's documented env auth, and reach for the
`docker-compose.cli-auth.yaml` overlay only as a fallback for an adapter that
genuinely reads the full CLI config directory.

---

## Best Practices

1. **Place `config.yaml` in project root** - Set `NOVA_PROJECT_ROOT` if the runtime starts elsewhere
2. **Never commit `config.yaml`** - It's already in `.gitignore`
3. **Use environment variables for secrets** - Don't hardcode API keys
4. **Keep `config.example.yaml` updated** - Document all new options
5. **Test configuration changes locally** - Before deploying
6. **Use Docker sandbox for production** - Better isolation and security

## Troubleshooting

### "Config file not found"

- Ensure `config.yaml` exists in the **project root** directory (`nova/config.yaml`)
- If the runtime starts outside the project root, set `NOVA_PROJECT_ROOT`
- Alternatively, set `NOVA_CONFIG_PATH` environment variable to point at a specific file

### "Invalid API key"

- Verify environment variables are set correctly
- Check that `$` prefix is used for env var references

### "Skills not loading"

- Check that `deer-flow/skills/` directory exists
- Verify skills have valid `SKILL.md` files
- Check `skills.path` or `NOVA_SKILLS_PATH` if using a custom path

### "Docker sandbox fails to start"

- Ensure Docker is running
- Check port 8080 (or configured port) is available
- Verify Docker image is accessible

## Examples

See `config.example.yaml` for complete examples of all configuration options.
