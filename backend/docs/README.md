# backend/docs

Developer-facing documentation for the Nova backend.

## Contents

### Core

- `API.md` — Gateway REST API reference
- `ARCHITECTURE.md` — Backend architecture details
- `CONFIGURATION.md` — Configuration options reference
- `SETUP.md` — Backend setup guide
- `TODO.md` — Outstanding work and planned features

### Features

- `FILE_UPLOAD.md` — File upload feature docs
- `PATH_EXAMPLES.md` — Virtual and physical path examples
- `STREAMING.md` — SSE streaming design and testing strategy
- `AUTO_TITLE_GENERATION.md` — Thread auto-title generation
- `EXECUTION_KERNEL.md` — Execution kernel and PTY manager
- `WORKSPACE_KERNEL.md` — Workspace intelligence kernel (WIK)
- `summarization.md` — Context summarization
- `plan_mode_usage.md` — Plan mode with TodoList
- `task_tool_improvements.md` — Task tool enhancements

### Security & Auth

- `AUTH_DESIGN.md` — Authentication architecture
- `AUTH_TEST_PLAN.md` — Auth test plan and coverage
- `AUTH_TEST_DOCKER_GAP.md` — Auth Docker test coverage gap
- `AUTH_UPGRADE.md` — Auth upgrade notes
- `GUARDRAILS.md` — Guardrail middleware setup and provider protocol

### Infrastructure

- `MCP_SERVER.md` — MCP server integration
- `IM_CHANNEL_CONNECTIONS.md` — User-owned IM channel connection setup
- `OPS_CONSOLE_API.md` — Ops console API surface
- `BLOCKING_IO_DETECTION.md` — Blocking IO detection and mitigation
- `SANDBOX_MEMORY_PROFILING.md` — Sandbox memory profiling
- `MEMORY_IMPROVEMENTS.md` — Memory system improvements
- `MEMORY_SETTINGS_REVIEW.md` — Memory settings review
- `middleware-execution-flow.md` — Middleware execution flow diagram
- `APPLE_CONTAINER.md` — Apple container support

### RFCs

- `rfc-create-deerflow-agent.md` — RFC: Create DeerFlow agent
- `rfc-extract-shared-modules.md` — RFC: Extract shared modules
- `rfc-grep-glob-tools.md` — RFC: Grep/glob tools

### Tests

- `REPLAY_E2E.md` — Replay E2E testing strategy

## Also see

- `backend/CLAUDE.md` — project rules for AI coding assistants
- `backend/Makefile` — `make test`, `make lint`, `make gateway`
