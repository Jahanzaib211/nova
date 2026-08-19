# Development Guide

> How to set up Nova for local development.

**Audience:** contributors, developers working on Nova.
**Last Updated:** Phase C9 (2026-07-17)
**Related:** [CONTRIBUTING.md](CONTRIBUTING.md), [backend/CLAUDE.md](backend/CLAUDE.md), [frontend/CLAUDE.md](frontend/CLAUDE.md)

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/Jahanzaib211/nova.git
cd nova
make check            # verify system requirements
make install          # install all deps (frontend + backend)

# 2. Configure
cp config.example.yaml config.yaml
export OPENAI_API_KEY="your-key-here"

# 3. Run
make dev              # starts Gateway + Frontend + Nginx
```

Open <http://localhost:2026>

---

## Prerequisites

- Python 3.12+
- Node.js 22+
- pnpm 10.26.2+
- uv (Python package manager)
- Docker (optional, for Docker sandbox)

---

## Development Modes

| Mode | Command | Use Case |
|------|---------|----------|
| `make dev` | Full stack (Gateway + Frontend + Nginx) | Standard development |
| `make gateway` | Backend only (port 8001) | Backend-only work |
| `pnpm dev` | Frontend only (port 3000) | Frontend-only work |
| `make docker-start` | Docker containers | Consistent environment |

---

## Project Structure

```
nova/
├── backend/
│   ├── packages/harness/deerflow/   # Agent framework (deerflow.*)
│   │   ├── agents/                  # LangGraph agent system
│   │   │   ├── lead_agent/          # Main agent (factory + system prompt)
│   │   │   ├── middlewares/         # 19 middleware components
│   │   │   ├── memory/              # Memory extraction, queue, prompts
│   │   │   └── thread_state.py      # ThreadState schema
│   │   ├── sandbox/                 # Sandbox execution system
│   │   │   ├── local/               # Local filesystem provider
│   │   │   ├── sandbox.py           # Abstract Sandbox interface
│   │   │   ├── tools.py             # bash, ls, read/write/str_replace
│   │   │   └── middleware.py        # Sandbox lifecycle management
│   │   ├── subagents/               # Subagent delegation system
│   │   │   ├── builtins/            # general-purpose, bash agents
│   │   │   ├── executor.py          # Background execution engine
│   │   │   └── registry.py          # Agent registry
│   │   ├── tools/builtins/          # Built-in tools (present_files, ask_clarification, view_image)
│   │   ├── mcp/                     # MCP integration (tools, cache, client)
│   │   ├── models/                  # Model factory with thinking/vision support
│   │   ├── skills/                  # Skills discovery, loading, parsing
│   │   ├── config/                  # Configuration system (app, model, sandbox, tool, etc.)
│   │   ├── community/               # Community tools (tavily, jina_ai, firecrawl, image_search, aio_sandbox)
│   │   ├── reflection/              # Dynamic module loading (resolve_variable, resolve_class)
│   │   ├── utils/                   # Utilities (network, readability)
│   │   └── client.py                # Embedded Python client (NovaClient)
│   │   └── workspace/               # Workspace Intelligence Kernel (Phase C9)
│   │       ├── models/              # Frozen dataclasses: Node, Edge, Symbol, Command, ExecutionPlan, WorkspaceSnapshot
│   │       ├── detectors/           # Fingerprint, Project, Language, Command detectors
│   │       ├── parsers/             # Python ast, JS regex parsers
│   │       ├── graph/               # SymbolIndex, WorkspaceGraph, DependencyGraph, CommandRegistry
│   │       ├── planner/             # WorkspacePlanner, RiskAnalyzer, PlanValidator
│   │       ├── cache/               # Disk-backed JSON cache with LRU eviction
│   │       ├── events/              # WIK domain events: WorkspaceScanned, PlanBuilt, CacheHit, etc.
│   │       ├── metrics/             # WIKMetrics: scan duration, symbol count, cache hit rate
│   │       └── services/implementations.py  # WorkspaceIntelligenceServiceImpl
│   ├── app/                         # Application layer (app.*)
│   │   ├── gateway/                 # FastAPI Gateway API
│   │   │   ├── app.py               # FastAPI application
│   │   │   └── routers/             # FastAPI route modules
│   │   └── channels/                # IM platform integrations
│   ├── tests/                       # Test suite
│   └── docs/                        # Documentation
├── frontend/                        # Next.js frontend application
│   ├── src/
│   │   ├── app/                     # Next.js App Router
│   │   ├── components/              # React components
│   │   ├── core/                    # Business logic (hooks, api, artifacts, threads, etc.)
│   │   └── ...
│   └── tests/                       # Unit + E2E tests
├── scripts/                         # Operations scripts
├── docker/                          # Docker configurations
├── config/                          # Cloudflare tunnel config
├── skills/                          # Agent skills directory
│   ├── public/                      # Public skills (committed)
│   └── custom/                      # Custom skills (gitignored)
├── docs/                            # Documentation
├── .opencode/                       # Opencode skills/commands
└── .kilo/                           # Kilo worktrees (experimental)
```

---

## Commands

### Root (full application)

```bash
make check            # Check system requirements
make install          # Install all dependencies (frontend + backend)
make dev              # Start all services (Gateway + Frontend + Nginx)
make start            # Start production services locally
make stop             # Stop all services
```

### Backend (backend development only)

```bash
cd backend
make install            # Install backend dependencies
make dev                # Run Gateway API with reload (port 8001)
make gateway            # Run Gateway API only (port 8001)
make test               # Run all backend tests
make test-blocking-io   # Run strict Blockbuster runtime gate on tests/blocking_io/
make lint               # Lint with ruff
make format             # Format code with ruff
```

The `detect-blocking-io` target parses `app/`, `packages/harness/deerflow/`,
and `scripts/` with AST. By default it reports only blocking IO candidates that
are inside async code, reachable from async code in the same file, or reachable
from sync-only `AgentMiddleware` before/after hooks that LangGraph can execute
on the async graph path. It prints a concise summary and writes complete JSON
findings to `.deer-flow/blocking-io-findings.json` at the repository root
(both `make detect-blocking-io` from the repo root and `cd backend && make
detect-blocking-io` resolve to the same repo-root path). JSON findings include
`priority`, `location`, `blocking_call`, `event_loop_exposure`, `reason`, and
`code` for model-assisted or manual review. `priority` is a deterministic
review ordering from operation type, not proof of a bug. Bare-name same-file
calls are resolved by function name, so duplicate helper names in one file can
conservatively over-report async reachability. It is intentionally
informational and is not run from CI in this round.

For a diff-scoped view of the same findings, `scripts/scan_changed_blocking_io.py`
(repo root) reports findings on the added lines of `git diff <base>...HEAD`
plus findings new versus the merge base (so a new async caller exposing an
untouched sync helper in the same file is still reported) — used by the
`blocking-io-guard` skill (`.agent/skills/blocking-io-guard/`) as the
deterministic scope step before routing each candidate to a fix and/or a
`tests/blocking_io/` runtime anchor.

Regression tests related to Docker/provisioner behavior:

- `tests/test_docker_sandbox_mode_detection.py` (mode detection from `config.yaml`)
- `tests/test_provisioner_kubeconfig.py` (kubeconfig file/directory handling)

Blocking-IO runtime gate (`tests/blocking_io/`):

- Wraps every item under `tests/blocking_io/` with a strict Blockbuster
  context scoped to `app.*` and `deerflow.*` (see
  `tests/support/detectors/blocking_io_runtime.py`). Any sync blocking IO
  call whose stack passes through DeerFlow business code while running on
  the asyncio event loop raises `BlockingError` and fails the test.
- Regression anchors live there: `test_skills_load.py` (locks the
  `asyncio.to_thread` offload around `LocalSkillStorage.load_skills`, fix
  for #1917); `test_sqlite_lifespan.py` (locks the offload around
  SQLite path resolution plus `ensure_sqlite_parent_dir`, fix for #1912);
  `test_jsonl_run_event_store.py` (locks `JsonlRunEventStore`'s async
  API offloading its file IO via `asyncio.to_thread`, fix #3084); and
  `test_uploads_middleware.py` (locks `UploadsMiddleware.abefore_agent`
  offloading the uploads-directory scan off the event loop).
- `test_gate_smoke.py` is a meta-test asserting the gate actually catches
  unoffloaded blocking IO and that the `@pytest.mark.allow_blocking_io`
  opt-out works.
- Coverage boundary: the gate only sees code that test execution actually
  touches. Static AST coverage is a separate concern (out of scope for
  this PR).
- CI: runs on every PR via `.github/workflows/backend-blocking-io-tests.yml`,
  hard-fail.

Boundary check (harness → app import firewall):

- `tests/test_harness_boundary.py` — ensures `packages/harness/deerflow/` never imports from `app.*`

CI runs these regression tests for every pull request via `.github/workflows/backend-unit-tests.yml`.

---

## Architecture

### Harness / App Split

The backend is split into two layers with a strict dependency direction:

- **Harness** (`packages/harness/deerflow/`): Publishable agent framework package (`deerflow-harness`). Import prefix: `deerflow.*`. Contains agent orchestration, tools, sandbox, models, MCP, skills, config — everything needed to build and run agents.
- **App** (`app/`): Unpublished application code. Import prefix: `app.*`. Contains the FastAPI Gateway API and IM channel integrations (Feishu, Slack, Telegram, DingTalk).

**Dependency rule**: App imports deerflow, but deerflow never imports app. This boundary is enforced by `tests/test_harness_boundary.py` which runs in CI.

**Import conventions**:

```python
# Harness internal
from deerflow.agents import make_lead_agent
from deerflow.models import create_chat_model

# App internal
from app.gateway.app import app
from app.channels.service import start_channel_service

# App → Harness (allowed)
from deerflow.config import get_app_config

# Harness → App (FORBIDDEN — enforced by test_harness_boundary.py)
# from app.gateway.routers.uploads import ...  # ← will fail CI
```

---

## Configuration System

### Main Configuration (`config.yaml`)

Setup: Copy `config.example.yaml` to `config.yaml` in the **project root** directory.

**Config Versioning**: `config.example.yaml` has a `config_version` field. On startup, `AppConfig.from_file()` compares user version vs example version and emits a warning if outdated. Missing `config_version` = version 0. Run `make config-upgrade` to auto-merge missing fields. When changing the config schema, bump `config_version` in `config.example.yaml`.

**Config Caching**: `get_app_config()` caches the parsed config, but automatically reloads it when the resolved config path or file content signature changes. The signature includes file metadata and a content digest, so Gateway and LangGraph reads stay aligned with `config.yaml` edits even on object-store or network mounts where mtime can remain stale.

**Config Hot-Reload Boundary**: Gateway dependencies route through `get_app_config()` on every request, so per-run fields like `models[*].max_tokens`, `summarization.*`, `title.*`, `memory.*`, `subagents.*`, `tools[*]`, and the agent system prompt pick up `config.yaml` edits on the next message. `AppConfig` is intentionally **not** cached on `app.state` — `lifespan()` keeps a local `startup_config` variable for one-shot bootstrap work and passes it to `langgraph_runtime(app, startup_config)`.

Infrastructure fields are **restart-required**. The authoritative list lives in `packages/harness/deerflow/config/reload_boundary.py::STARTUP_ONLY_FIELDS` and is mirrored by the standardised `"startup-only:"` prefix on the corresponding `Field(description=...)` in `AppConfig`, so IDE hover on those fields surfaces the reason inline (no need to context-switch into this table). Currently registered: `database`, `checkpointer`, `run_events`, `stream_bridge`, `sandbox`, `log_level`, `channels`, `channel_connections`. Adding a new restart-required field requires updating the registry; drift is pinned by `tests/test_reload_boundary.py`.

Configuration priority:

1. Explicit `config_path` argument
2. `DEER_FLOW_CONFIG_PATH` environment variable
3. `config.yaml` in current directory (backend/)
4. `config.yaml` in parent directory (project root - **recommended location**)

Levels 1 and 2 are **strict** — a path that is set but missing raises `FileNotFoundError` instead of falling through, so a wrong `DEER_FLOW_CONFIG_PATH` stops the gateway at import.

**In Docker, `DEER_FLOW_CONFIG_PATH` / `DEER_FLOW_EXTENSIONS_CONFIG_PATH` must be pinned in the compose service's `environment:` block.** The repo-root `.env` holds *host* paths (it has to — `docker-compose.yaml` uses `${DEER_FLOW_CONFIG_PATH}` as a bind-mount source), and `env_file: ../.env` leaks them into any container that does not override them; `environment:` wins over `env_file:`. Omitting the pin is a deferred failure — the container starts, the gateway dies at import, and nginx reports `gateway could not be resolved`, which looks like DNS. Took the public deployment down on 2026-08-17. See [backend/docs/CONFIGURATION.md](backend/docs/CONFIGURATION.md#docker-config-paths-vs-env_file).

Config values starting with `$` are resolved as environment variables (e.g., `$OPENAI_API_KEY`).
`ModelConfig` also declares `use_responses_api` and `output_version` so OpenAI `/v1/responses` can be enabled explicitly while still using `langchain_openai:ChatOpenAI`.

### Extensions Configuration (`extensions_config.json`)

MCP servers and skills are configured together in `extensions_config.json` in project root:

Configuration priority:

1. Explicit `config_path` argument
2. `DEER_FLOW_EXTENSIONS_CONFIG_PATH` environment variable
3. `extensions_config.json` in current directory (backend/)
4. `extensions_config.json` in parent directory (project root - **recommended location**)

---

## Development

### Commands

```bash
make install    # Install dependencies
make dev        # Run Gateway API + embedded agent runtime (port 8001)
make gateway    # Run Gateway API without reload (port 8001)
make test       # Run all backend tests
make test-blocking-io   # Run strict Blockbuster runtime gate on tests/blocking_io/
make lint       # Run linter (ruff)
make format     # Format code with ruff
```

### Code Style

- **Linter/Formatter**: `ruff`
- **Line length**: 240 characters
- **Python**: 3.12+ with type hints
- **Quotes**: Double quotes
- **Indentation**: 4 spaces

### Testing

```bash
uv run pytest
```

`make detect-blocking-io` statically scans backend business code for blocking
IO that may run on the backend event loop and is not test-coverage-bound. It
prints a concise summary for human review and writes complete JSON findings to
`.deer-flow/blocking-io-findings.json` at the repository root (regardless of
whether the target is invoked from the repo root or from `backend/`). JSON
findings include both broad IO category and review-oriented fields such as
`priority`, `location`, `blocking_call`, `event_loop_exposure`, `reason`, and
`code`. `priority` is a deterministic review ordering from the operation type,
not proof of a bug. Bare-name same-file calls are resolved by function name,
so duplicate helper names in one file can conservatively over-report async
reachability.

For a diff-scoped view of the same findings, `scripts/scan_changed_blocking_io.py`
(repo root) reports findings on the added lines of `git diff <base>...HEAD`
plus findings new versus the merge base (so a new async caller exposing an
untouched sync helper in the same file is still reported) — used by the
`blocking-io-guard` skill (`.agent/skills/blocking-io-guard/`) as the
deterministic scope step before routing each candidate to a fix and/or a
`tests/blocking_io/` runtime anchor.

---

## Running the Full Application

From the **project root** directory:

```bash
make dev
```

This starts all services and makes the application available at `http://localhost:2026`.

**All startup modes:**

| | **Local Foreground** | **Local Daemon** | **Docker Dev** | **Docker Prod** |
|---|---|---|---|---|
| **Dev** | `./scripts/serve.sh --dev`<br/>`make dev` | `./scripts/serve.sh --dev --daemon`<br/>`make dev-daemon` | `./scripts/docker.sh start`<br/>`make docker-start` | — |
| **Prod** | `./scripts/serve.sh --prod`<br/>`make start` | `./scripts/serve.sh --prod --daemon`<br/>`make start-daemon` | — | `./scripts/deploy.sh`<br/>`make up` |

| Action | Local | Docker Dev | Docker Prod |
|---|---|---|---|
| **Stop** | `./scripts/serve.sh --stop`<br/>`make stop` | `./scripts/docker.sh stop`<br/>`make docker-stop` | `./scripts/deploy.sh down`<br/>`make down` |
| **Restart** | `./scripts/serve.sh --restart [flags]` | `./scripts/docker.sh restart` | — |

**Nginx routing**:

- `/api/langgraph/*` → Gateway embedded runtime (8001), rewritten to `/api/*`
- `/api/*` (other) → Gateway API (8001)
- `/` (non-API) → Frontend (3000)

---

## Running Backend Services Separately

From the **backend** directory:

```bash
# Gateway API
make gateway
```

Direct access (without nginx):

- Gateway: `http://localhost:8001`

---

## Frontend Configuration

The frontend uses environment variables to connect to backend services:

- `NEXT_PUBLIC_LANGGRAPH_BASE_URL` - Defaults to `/api/langgraph` (through nginx)
- `NEXT_PUBLIC_BACKEND_BASE_URL` - Defaults to empty string (through nginx)

When using `make dev` from root, the frontend automatically connects through nginx.

---

## Key Features

### File Upload

Multi-file upload with automatic document conversion:

- Endpoint: `POST /api/threads/{thread_id}/uploads`
- Supports: PDF, PPT, Excel, Word documents (converted via `markitdown`)
- Rejects directory inputs before copying so uploads stay all-or-nothing
- Reuses one conversion worker per request when called from an active event loop
- Files stored in thread-isolated directories under the resolving user's bucket (`users/{user_id}/threads/{thread_id}/user-data/uploads`). For IM channels the owner is threaded explicitly via the `user_id=` kwarg (see IM Channels → Owner-scoped file storage); HTTP/embedded callers resolve it from `get_effective_user_id()`
- Duplicate filenames in a single upload request are auto-renamed with `_N` suffixes so later files do not truncate earlier files
- Agent receives uploaded file list via `UploadsMiddleware`

See [docs/FILE_UPLOAD.md](docs/FILE_UPLOAD.md) for details.

### Plan Mode

TodoList middleware for complex multi-step tasks:

- Controlled via runtime config: `config.configurable.is_plan_mode = True`
- Provides `write_todos` tool for task tracking
- One task in_progress at a time, real-time updates

See [docs/plan_mode_usage.md](docs/plan_mode_usage.md) for details.

### Context Summarization

Automatic conversation summarization when approaching token limits:

- Configured in `config.yaml` under `summarization` key
- Trigger types: tokens, messages, or fraction of max input
- Keeps recent messages while summarizing older ones

See [docs/summarization.md](docs/summarization.md) for details.

### Vision Support

For models with `supports_vision: true`:

- `ViewImageMiddleware` processes images in conversation
- `view_image_tool` added to agent's toolset
- Images automatically converted to base64 and injected into state

---

## Code Style

- Uses `ruff` for linting and formatting
- Line length: 240 characters
- Python 3.12+ with type hints
- Double quotes, space indentation

---

## Documentation

See `docs/` directory for detailed documentation:

- [CONFIGURATION.md](backend/docs/CONFIGURATION.md) - Configuration options
- [ARCHITECTURE.md](backend/docs/ARCHITECTURE.md) - Architecture details
- [API.md](backend/docs/API.md) - API reference
- [SETUP.md](backend/docs/SETUP.md) - Setup guide
- [FILE_UPLOAD.md](backend/docs/FILE_UPLOAD.md) - File upload feature
- [PATH_EXAMPLES.md](backend/docs/PATH_EXAMPLES.md) - Path types and usage
- [summarization.md](backend/docs/summarization.md) - Context summarization
- [plan_mode_usage.md](backend/docs/plan_mode_usage.md) - Plan mode with TodoList

---

## License

See the [LICENSE](./LICENSE) file in the project root.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

**Quick start:**

1. Fork the repo and create a feature branch
2. Run `make setup` (Docker) or `make install` (local)
3. Make changes with hot-reload enabled
4. Run `cd backend && uv run pytest` and `cd frontend && pnpm test`
5. Submit a PR — CI will run format, lint, typecheck, and tests

Regression coverage includes Docker sandbox mode detection and provisioner kubeconfig-path handling tests in `backend/tests/`.
Backend blocking-IO diagnostics are available from the repository root with
`make detect-blocking-io`: it statically scans backend business code for
blocking IO that may run on the backend event loop, prints a concise summary,
and writes complete JSON findings to `.deer-flow/blocking-io-findings.json`.
The JSON includes compact review records with `priority`, `location`,
`blocking_call`, `event_loop_exposure`, `reason`, and `code`.
Gateway artifact serving now forces active web content types (`text/html`, `application/xhtml+xml`, `image/svg+xml`) to download as attachments instead of inline rendering, reducing XSS risk for generated artifacts.
