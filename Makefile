# DeerFlow - Unified Development Environment

.PHONY: help config config-upgrade check install setup doctor detect-thread-boundaries detect-blocking-io dev dev-daemon start start-daemon stop up down clean docker-init docker-start docker-stop docker-logs docker-logs-frontend docker-logs-gateway

BASH ?= bash
BACKEND_UV_RUN = cd backend && uv run

# Detect OS for Windows compatibility
ifeq ($(OS),Windows_NT)
    SHELL := cmd.exe
    PYTHON ?= python
    # Run repo shell scripts through Git Bash when Make is launched from cmd.exe / PowerShell.
    RUN_WITH_GIT_BASH = call scripts\run-with-git-bash.cmd
else
    PYTHON ?= python3
    RUN_WITH_GIT_BASH =
endif

help:
	@echo "DeerFlow Development Commands:"
	@echo "  make setup           - Interactive setup wizard (recommended for new users)"
	@echo "  make doctor          - Check configuration and system requirements"
	@echo "  make config          - Generate local config files (aborts if config already exists)"
	@echo "  make config-upgrade  - Merge new fields from config.example.yaml into config.yaml"
	@echo "  make check           - Check if all required tools are installed"
	@echo "  make detect-thread-boundaries - Inventory async/thread boundary points"
	@echo "  make detect-blocking-io        - Inventory blocking IO that may block the backend event loop"
	@echo "  make install         - Install all dependencies (frontend + backend + pre-commit hooks)"
	@echo "  make setup-sandbox   - Pre-pull sandbox container image (recommended)"
	@echo "  make dev             - Start all services in development mode (with hot-reloading)"
	@echo "  make dev-daemon      - Start dev services in background (daemon mode)"
	@echo "  make start           - Start all services in production mode (optimized, no hot-reloading)"
	@echo "  make start-daemon    - Start prod services in background (daemon mode)"
	@echo "  make stop            - Stop all running services"
	@echo "  make clean           - Clean up processes and temporary files"
	@echo ""
	@echo "Docker Production Commands:"
	@echo "  make up              - Build and start production Docker services (localhost:2026)"
	@echo "  make down            - Stop and remove production Docker containers"
	@echo ""
	@echo "Docker Development Commands:"
	@echo "  make docker-init     - Pull the sandbox image"
	@echo "  make docker-start    - Start Docker services (mode-aware from config.yaml, localhost:2026)"
	@echo "  make docker-stop     - Stop Docker development services"
	@echo "  make docker-logs     - View Docker development logs"
	@echo "  make docker-logs-frontend - View Docker frontend logs"
	@echo "  make docker-logs-gateway - View Docker gateway logs"

## Setup & Diagnosis
setup:
	@$(BACKEND_UV_RUN) python ../scripts/setup_wizard.py

doctor:
	@$(BACKEND_UV_RUN) python ../scripts/doctor.py

detect-thread-boundaries:
	@$(PYTHON) ./scripts/detect_thread_boundaries.py

detect-blocking-io:
	@$(MAKE) -C backend detect-blocking-io

config:
	@$(PYTHON) ./scripts/configure.py

config-upgrade:
	@$(RUN_WITH_GIT_BASH) ./scripts/config-upgrade.sh

# Check required tools
check:
	@$(PYTHON) ./scripts/check.py

# AI self-test: enforce cross-project isolation (see AGENTS.md)
cross-ref-check:
	@bash ./scripts/check_no_cross_references.sh

# Apply the auto-fix tool to all detected cross-reference violations
fix-cross-refs:
	@$(PYTHON) ./scripts/fix_cross_references.py

fix-cross-refs-dry-run:
	@$(PYTHON) ./scripts/fix_cross_references.py --dry-run

# Install all dependencies
install:
	@echo "Installing backend dependencies..."
	@cd backend && uv sync
	@echo "Installing frontend dependencies..."
	@cd frontend && pnpm install
	@echo "Installing pre-commit hooks..."
	@uv tool install pre-commit
	@pre-commit install --overwrite
	@echo "✓ All dependencies installed"
	@echo ""
	@echo "=========================================="
	@echo "  Optional: Pre-pull Sandbox Image"
	@echo "=========================================="
	@echo ""
	@echo "If you plan to use Docker/Container-based sandbox, you can pre-pull the image:"
	@echo "  make setup-sandbox"
	@echo ""

# Pre-pull sandbox Docker image (optional but recommended)
setup-sandbox:
	@$(RUN_WITH_GIT_BASH) ./scripts/setup-sandbox.sh

# Start all services in development mode (with hot-reloading)
dev:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --dev

# Start all services in production mode (with optimizations)
start:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --prod

# Start all services in daemon mode (background)
dev-daemon:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --dev --daemon

# Start prod services in daemon mode (background)
start-daemon:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --prod --daemon

# Stop all services
stop:
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --stop

# Clean up
clean: stop
	@echo "Cleaning up..."
	@-rm -rf backend/.deer-flow 2>/dev/null || true
	@-rm -rf logs/*.log 2>/dev/null || true
	@echo "✓ Cleanup complete"

# ==========================================
# Docker Development Commands
# ==========================================

# Initialize Docker containers and install dependencies
docker-init:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh init

# Start Docker development environment
docker-start:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh start

# Stop Docker development environment
docker-stop:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh stop

# View Docker development logs
docker-logs:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh logs

# View Docker development logs
docker-logs-frontend:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh logs --frontend
docker-logs-gateway:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh logs --gateway

# ==========================================
# Production Docker Commands
# ==========================================

# Build and start production services
up:
	@$(RUN_WITH_GIT_BASH) ./scripts/deploy.sh

# Stop and remove production containers
down:
	@$(RUN_WITH_GIT_BASH) ./scripts/deploy.sh down

# ==========================================
# AMD Hackathon (Act II) submission automation
# ==========================================
# Override REGISTRY with your registry, e.g.:
#   make hackathon-track1-submit REGISTRY=ghcr.io/your-org
REGISTRY ?= ghcr.io/jahanzaib211
TRACK1_TAG ?= latest
TRACK1_IMAGE = $(REGISTRY)/nova-track1:$(TRACK1_TAG)

.PHONY: hackathon-track1-build hackathon-track1-smoke hackathon-track1-push hackathon-track1-verify hackathon-track1 hackathon-track1-submit

# Local build (linux/amd64, loaded into the local docker for smoke testing).
hackathon-track1-build:
	docker buildx build --platform linux/amd64 -t $(TRACK1_IMAGE) --load hackathon/track1

# Hermetic unit test + in-container contract run (bogus endpoint → valid output, exit 0).
hackathon-track1-smoke:
	cd backend && uv run pytest ../hackathon/track1/test_agent.py -q
	rm -rf hackathon/track1/out && mkdir -p hackathon/track1/out
	docker run --rm \
	  -e FIREWORKS_API_KEY=smoke -e FIREWORKS_BASE_URL=http://127.0.0.1:9/v1 \
	  -e ALLOWED_MODELS=gemma-4-31b-it -e TRACK1_CONCURRENCY=2 \
	  -v "$(PWD)/hackathon/track1/sample:/input:ro" \
	  -v "$(PWD)/hackathon/track1/out:/output" \
	  $(TRACK1_IMAGE)
	$(PYTHON) -c "import json;d=json.load(open('hackathon/track1/out/results.json'));assert isinstance(d,list) and all('task_id' in x and 'answer' in x for x in d);print('contract OK:',len(d),'results')"

# Build straight to the registry for linux/amd64 (public push).
hackathon-track1-push:
	docker buildx build --platform linux/amd64 -t $(TRACK1_IMAGE) --push hackathon/track1

# Verify the pushed image: public, linux/amd64 manifest, <= 10GB.
hackathon-track1-verify:
	@$(RUN_WITH_GIT_BASH) ./scripts/hackathon-verify-image.sh $(TRACK1_IMAGE)

# Local pipeline: build then smoke.
hackathon-track1: hackathon-track1-build hackathon-track1-smoke

# Full automated submission: smoke locally, push, then verify the public image.
hackathon-track1-submit: hackathon-track1-build hackathon-track1-smoke hackathon-track1-push hackathon-track1-verify
	@echo "Track 1 image submitted: $(TRACK1_IMAGE)"

.PHONY: hackathon-track3-deck hackathon-track3-prescreen
# Render the Track 3 slide deck HTML → PDF (needs google-chrome/chromium).
hackathon-track3-deck:
	@CHROME=$$(command -v google-chrome || command -v chromium || command -v chromium-browser); \
	  [ -n "$$CHROME" ] || { echo "no chrome/chromium found" >&2; exit 1; }; \
	  "$$CHROME" --headless --disable-gpu --no-sandbox --no-pdf-header-footer \
	    --print-to-pdf=hackathon/track3/deck.pdf "file://$(PWD)/hackathon/track3/deck.html"
	@echo "wrote hackathon/track3/deck.pdf"

# Self-audit the Track 3 submission (repo evidence; live demo if NOVA_LIVE_URL set).
hackathon-track3-prescreen:
	@$(RUN_WITH_GIT_BASH) ./scripts/hackathon-track3-prescreen.sh
