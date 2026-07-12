# Development Guide

> How to set up Nova for local development.

**Audience:** contributors, developers working on Nova.
**Last Updated:** Phase C1 (2026-07-12)
**Related:** [CONTRIBUTING.md](CONTRIBUTING.md), [backend/CLAUDE.md](backend/CLAUDE.md), [frontend/CLAUDE.md](frontend/CLAUDE.md)

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

Open http://localhost:2026

## Prerequisites

- Python 3.12+
- Node.js 22+
- pnpm 10.26.2+
- uv (Python package manager)
- Docker (optional, for Docker sandbox)

## Development Modes

| Mode | Command | Use Case |
|------|---------|----------|
| `make dev` | Full stack (Gateway + Frontend + Nginx) | Standard development |
| `make gateway` | Backend only (port 8001) | Backend-only work |
| `pnpm dev` | Frontend only (port 3000) | Frontend-only work |
| `make docker-start` | Docker containers | Consistent environment |

## Project Structure

```
nova/
├── backend/
│   ├── packages/harness/deerflow/   # Agent framework (deerflow.*)
│   │   ├── agents/                  # LangGraph agent system
│   │   ├── middlewares/             # 19 agent middleware components
│   │   ├── runtime/                 # RunManager, StreamBridge, diagnostics
│   │   ├── persistence/             # ORM models, repositories, migrations
│   │   ├── services/                # Typed service layer (Phase C1)
│   │   ├── sandbox/                 # Sandbox execution
│   │   ├── tools/                   # Agent tools
│   │   ├── mcp/                     # MCP integration
│   │   ├── models/                  # Model factory
│   │   └── skills/                  # Skills system
│   ├── app/                         # Application layer (app.*)
│   │   └── gateway/                 # FastAPI Gateway API
│   └── tests/                       # Test suite
├── frontend/
│   ├── src/                         # Next.js application
│   └── tests/                       # Unit + E2E tests
├── scripts/                         # Operations scripts
├── docker/                          # Docker configurations
├── config/                          # Cloudflare tunnel config
└── docs/                            # Documentation
```

## Commands

### Root (full application)

```bash
make check            # Check system requirements
make install          # Install all dependencies
make dev              # Start all services
make stop             # Stop all services
```

### Backend

```bash
cd backend
make dev              # Run Gateway with reload (port 8001)
make test             # Run all backend tests
make lint             # Lint with ruff
make format           # Format code with ruff
```

### Frontend

```bash
cd frontend
pnpm dev              # Dev server (port 3000)
pnpm check            # Lint + type check
pnpm test             # Unit tests with Vitest
pnpm test:e2e         # E2E tests with Playwright
```

## Testing

### Self-test protocol (before every commit)

```bash
cd backend && PYTHONPATH=../scripts \
  uv run pytest tests/test_healthcheck_daemon.py tests/test_no_cross_references.py -v
cd frontend && pnpm check && pnpm test
git status
```

### Running all tests

```bash
# Backend
cd backend && make test

# Frontend
cd frontend && pnpm test
```

## Database

Nova uses SQLite by default (no setup needed). For schema changes:
- Create an Alembic migration file in `backend/packages/harness/deerflow/persistence/migrations/versions/`
- Always make migrations idempotent
- See `docs/RUNBOOK.md` §8 for workflow

## Architecture

- **Harness/App split**: `packages/harness/deerflow/` (publishable framework, `deerflow.*`) vs `app/` (application code, `app.*`). Harness never imports app.
- **Middleware chain**: 19 middleware components in strict append order.
- **Agent runtime**: embedded in Gateway via `RunManager` + `run_agent()` + `StreamBridge`.
- **Sandbox**: per-thread isolated execution environments (local or Docker).
- **Streaming**: SSE with correlation_id for cross-process tracing.

See [backend/CLAUDE.md](backend/CLAUDE.md) for full architecture.
