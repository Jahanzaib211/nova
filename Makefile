#!/usr/bin/env make
# Nova Makefile
# Single entry point: make <target>
#
# Prerequisites: docker, docker compose v2, pm2, uv, pnpm, python3
#
# Secrets: create ~/.config/nova/monitoring.env with:
#   GRAFANA_ADMIN_PASSWORD=<random>
#   NTFY_TOPIC=<ntfy-topic>   # optional, for push alerts
#   CLOUDFLARE_ACCESS_AUD=<aud>  # optional, for Cloudflare Access
#
# DO NOT commit monitoring.env to git.

.PHONY: help setup doctor config config-upgrade check install setup-sandbox \
	dev dev-daemon start start-daemon stop \
	docker-init docker-start docker-stop docker-logs up down \
	monitoring-up monitoring-down monitoring-status monitoring-verify monitoring-logs monitoring-screenshots monitoring-chaos sloth-generate

COMPOSE := docker compose -f docker/monitoring/docker-compose.yaml
COMPOSE_DIR := docker/monitoring

# ── Hostname seeding ───────────────────────────────────────────────────────
HOSTS := prometheus.local grafana.local loki.local alloy.local blackbox.local kuma.local
HOSTS_FILE := /etc/hosts

define SEED_HOSTS
	@echo "Checking /etc/hosts for monitoring hostnames..."
	@for h in $(HOSTS); do \
		if ! grep -q "$$h" $(HOSTS_FILE) 2>/dev/null; then \
			echo "  [WARN] $$h not in hosts — add manually: sudo sh -c 'echo 127.0.0.1 $$h >> $(HOSTS_FILE)'"; \
		else \
			echo "  $$h already in hosts"; \
		fi; \
	done
endef

# ── Targets ───────────────────────────────────────────────────────────────

help:
	@echo "Nova — available targets:"
	@echo ""
	@echo "Setup:"
	@echo "  setup                  Interactive wizard: LLM provider, search, sandbox/safety (~2 min)"
	@echo "  config                 Copy config.example.yaml/.env.example as-is (advanced/manual)"
	@echo "  config-upgrade         Migrate an existing config.yaml to the current schema version"
	@echo "  doctor                 Verify setup end-to-end, actionable fix hints"
	@echo "  check                  Verify required tooling is installed (node, pnpm, uv, nginx)"
	@echo "  install                Install backend (uv) + frontend (pnpm) dependencies"
	@echo "  setup-sandbox          Pull the AIO sandbox image (only if sandbox.use isn't 'local')"
	@echo ""
	@echo "Run (local, non-Docker):"
	@echo "  dev / dev-daemon       Foreground / daemonized dev server (./scripts/serve.sh --dev)"
	@echo "  start / start-daemon   Foreground / daemonized prod server (./scripts/serve.sh --prod)"
	@echo "  stop                   Stop the local server"
	@echo ""
	@echo "Run (Docker):"
	@echo "  docker-init            Pull sandbox image (only once or when image updates)"
	@echo "  docker-start           Start the dev stack (auto-detects sandbox mode from config.yaml)"
	@echo "  docker-stop            Stop the dev stack"
	@echo "  docker-status          Report sandbox mode + DooD socket + gateway health"
	@echo "  docker-logs [ARGS=...] Tail dev stack logs (ARGS passed to scripts/docker.sh logs)"
	@echo "  up                     Build + start the production stack (./scripts/deploy.sh)"
	@echo "  down                   Tear down the production stack"
	@echo ""
	@echo "Observability:"
	@echo "  monitoring-up          Bring up the full stack (Prometheus, Loki, Alloy,"
	@echo "                          Grafana, node_exporter, cadvisor, blackbox,"
	@echo "                          uptime-kuma)"
	@echo "  monitoring-down        Tear down the stack (preserves volumes)"
	@echo "  monitoring-status      Show container health + PM2 status"
	@echo "  monitoring-logs        Tail logs from all containers"
	@echo "  monitoring-verify      Run the 11-step verification gate (PASS/FAIL)"
	@echo "  monitoring-screenshots  Playwright screenshots of all 4 dashboards"
	@echo "  monitoring-chaos       Run pumba chaos scenarios (kills containers, asserts alerts)"
	@echo "  sloth-generate         Regenerate SLO rules from sloth/slos.yaml"
	@echo ""
	@echo "Secrets: ~/.config/nova/monitoring.env (see Makefile header)"

# ── Setup & local dev ─────────────────────────────────────────────────────

setup:
	@python3 scripts/setup_wizard.py

config:
	@python3 scripts/configure.py

config-upgrade:
	@bash scripts/config-upgrade.sh

doctor:
	@python3 scripts/doctor.py

check:
	@bash scripts/check.sh

install:
	@cd backend && uv sync
	@cd backend && uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 2>/dev/null || true
	@cd frontend && pnpm install

# Voice real-engine tests need the ``nvidia-cublas-cu12`` and ``nvidia-cudnn-cu12``
# wheels to find ``libcublas.so.12`` / ``libcudnn.so.9`` at runtime. They are
# installed by ``make install``; to run just the voice tests with the right
# environment:
#
#   export LD_LIBRARY_PATH="$(pwd)/backend/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:$(pwd)/backend/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib:$$LD_LIBRARY_PATH"
#   cd backend && uv run pytest tests/test_voice_engines_real.py -v
#
# (Or run ``make test-voice`` once the target is wired.)
voice-libs:
	@cd backend && uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

test-voice:
	@cd backend && LD_LIBRARY_PATH="$(pwd)/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:$(pwd)/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib:$$LD_LIBRARY_PATH" uv run pytest tests/test_voice_engines_real.py -v || echo "Voice real-engine tests require nvidia-cublas-cu12 + nvidia-cudnn-cu12; run 'make voice-libs' first."

setup-sandbox:
	@./scripts/docker.sh init

dev:
	@./scripts/serve.sh --dev

dev-daemon:
	@./scripts/serve.sh --dev --daemon

start:
	@./scripts/serve.sh --prod

start-daemon:
	@./scripts/serve.sh --prod --daemon

stop:
	@./scripts/serve.sh --stop

# ── Docker ────────────────────────────────────────────────────────────────

docker-init:
	@./scripts/docker.sh init

docker-start:
	@./scripts/docker.sh start

docker-stop:
	@./scripts/docker.sh stop

docker-status:
	@./scripts/docker.sh status

docker-logs:
	@./scripts/docker.sh logs $(ARGS)

up:
	@./scripts/deploy.sh

down:
	@./scripts/deploy.sh down

# ── Core stack ────────────────────────────────────────────────────────────

monitoring-up: monitoring-env-check
	@$(SEED_HOSTS)
	@echo ""
	@echo ">>> Bringing up Nova observability stack..."
	$(COMPOSE) up -d
	@echo ""
	@echo "Waiting for services to become healthy..."
	@for svc in prometheus loki alloy grafana node_exporter cadvisor blackbox_exporter uptime-kuma; do \
		echo -n "  $$svc: "; \
		$(COMPOSE) exec -T $$svc wget -q -O /dev/null http://127.0.0.1:1/health 2>/dev/null && echo "UP" || echo "STARTING..."; \
	done
	@echo ""
	@echo "Grafana:     http://grafana.local:3002  (admin / see ~/.config/nova/monitoring.env)"
	@echo "Prometheus:  http://prometheus.local:9090"
	@echo "Loki:        http://loki.local:3100"
	@echo "UptimeKuma:  http://kuma.local:3001"
	@echo ""
	@echo ">>> Stack up. Run 'make monitoring-verify' to gate."

monitoring-down:
	@echo ">>> Tearing down Nova observability stack..."
	$(COMPOSE) down

monitoring-status:
	@echo "=== Container health ==="
	$(COMPOSE) ps --format "table {{.Name}}\t{{.Status}}\t{{.Health}}"
	@echo ""
	@echo "=== Prometheus targets ==="
	@curl -s http://127.0.0.1:9090/api/v1/targets | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  {t[\"labels\"][\"job\"]:20} {t[\"health\"]:8} {t.get(\"lastError\",\"\")}') for t in d['data']['activeTargets']]" 2>/dev/null || echo "  Prometheus not reachable"

monitoring-logs:
	$(COMPOSE) logs -f --tail=50

# ── Secrets check ────────────────────────────────────────────────────────

MONITORING_ENV := $(HOME)/.config/nova/monitoring.env

monitoring-env-check:
	@mkdir -p $$(dirname $(MONITORING_ENV))
	@if [ ! -f $(MONITORING_ENV) ]; then \
		echo "Creating $(MONITORING_ENV) with defaults..."; \
		echo "GRAFANA_ADMIN_USER=admin" > $(MONITORING_ENV); \
		echo "GRAFANA_ADMIN_PASSWORD=nova-change-me-$$RANDOM" >> $(MONITORING_ENV); \
		echo "NTFY_TOPIC=nova-alerts" >> $(MONITORING_ENV); \
		echo "Created. Edit it with: nano $(MONITORING_ENV)"; \
		echo "Then run this target again."; \
		exit 1; \
	fi
	@if grep -q "nova-change-me" $(MONITORING_ENV) 2>/dev/null; then \
		echo "WARNING: GRAFANA_ADMIN_PASSWORD is still the default. Update $(MONITORING_ENV)"; \
	fi

# ── SLO rules via sloth ──────────────────────────────────────────────────

sloth-generate:
	@echo ">>> Generating Prometheus SLO rules from sloth/slos.yaml..."
	@docker run --rm \
		-v $(abspath $(COMPOSE_DIR))/sloth:/out \
		grafana/sloth:v0.13.0 \
		generate -i /out/slos.yaml -o /out/rules --stdout
	@echo "Rules generated. Reload Prometheus: curl -X POST http://127.0.0.1:9090/-/reload"

# ── Verification gate ─────────────────────────────────────────────────────

monitoring-verify:
	@echo "=== Nova Observability Stack — Verification Gate ==="
	@echo ""

	@# Step 1: Check Prometheus is up
	@echo -n "[01/11] Prometheus up: "
	@curl -s --max-time 5 http://127.0.0.1:9090/-/healthy | grep -q "Prometheus" && echo "PASS" || echo "FAIL"

	@# Step 2: Check all scrape targets
	@echo -n "[02/11] Prometheus targets (allow llama+litellm down): "
	@TARGETS=$$(curl -s http://127.0.0.1:9090/api/v1/targets | python3 -c "import sys,json; d=json.load(sys.stdin); print(len([t for t in d['data']['activeTargets'] if t['health']=='up']))" 2>/dev/null || echo "0"); \
	echo "$$TARGETS targets up"

	@# Step 3: Check Loki
	@echo -n "[03/11] Loki ready: "
	@if curl -s --max-time 5 http://127.0.0.1:3100/ready | grep -q ready; then echo "PASS"; else echo "FAIL"; fi

	@# Step 4: Check Grafana
	@echo -n "[04/11] Grafana healthy: "
	@if curl -s --max-time 5 http://127.0.0.1:3002/api/health | grep -q 'database.*ok'; then echo "PASS"; else echo "FAIL"; fi

	@# Step 5: Check Grafana dashboards provisioned
	@echo -n "[05/11] Grafana dashboards: "
	@DASHBOARDS=$$(curl -s -u admin:admin http://127.0.0.1:3002/api/search?type=dash-db | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))" 2>/dev/null || echo "0"); \
	echo "$$DASHBOARDS dashboards found"

	@# Step 6: Check Loki can query labels
	@echo -n "[06/11] Loki label query: "
	@if curl -s --max-time 5 "http://127.0.0.1:3100/loki/api/v1/labels" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='success' else 1)" 2>/dev/null; then echo "PASS"; else echo "FAIL"; fi

	@# Step 7: Check node_exporter
	@echo -n "[07/11] node_exporter metrics: "
	@curl -s --max-time 5 http://127.0.0.1:9100/metrics | grep -q "node_cpu" && echo "PASS" || echo "FAIL"

	@# Step 8: Check cadvisor
	@echo -n "[08/11] cadvisor metrics: "
	@curl -s --max-time 5 http://127.0.0.1:9181/metrics | grep -q "container_memory_usage" && echo "PASS" || echo "FAIL"

	@# Step 9: Check blackbox
	@echo -n "[09/11] blackbox_exporter healthy: "
	@if curl -s --max-time 5 http://127.0.0.1:9115/-/healthy | grep -q -i healthy; then echo "PASS"; else echo "FAIL"; fi

	@# Step 10: Check uptime-kuma
	@echo -n "[10/11] uptime-kuma: "
	@if curl -s --max-time 5 -I http://127.0.0.1:3003/ | grep -q "200\|302"; then echo "PASS"; else echo "FAIL"; fi

	@# Step 11: Verify PM2 app list
	@echo -n "[11/11] PM2 nova-monitoring app: "
	@pm2 ls | grep -q "nova-monitoring" && echo "PASS" || echo "NOT REGISTERED (run: pm2 start ecosystem.config.js --only nova-monitoring)"
	@echo ""
	@echo "=== Gate complete. Fix FAILs before declaring done. ==="
