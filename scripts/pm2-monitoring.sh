#!/bin/sh
# PM2 wrapper for the Nova observability stack (Grafana/Loki/Prometheus/Alloy).
# Runs docker compose in the foreground so PM2 owns the lifecycle.
# All services are loopback-bound — no LAN exposure.
#
# NOT REGISTERED IN ecosystem.config.js, DELIBERATELY.
#
# The `nova-monitoring` app was removed on 2026-08-13 because it duplicates the
# k3s `monitoring` namespace, which is the live one on this host (verified
# 2026-08-20: namespace active 21d, alertmanager running). Two Prometheus
# instances scraping the same targets is not redundancy — it is two sources of
# truth that disagree during exactly the incident you need them for.
#
# This script is kept, not deleted, because the compose stack is the right
# answer for a deployment WITHOUT k3s. If you enable it here, retire the k8s
# monitoring namespace first; do not run both.
#
# Start it manually with:  sh scripts/pm2-monitoring.sh
set -eu

COMPOSE_DIR="${DEER_FLOW_ROOT:-$(dirname "$(dirname "$0")")}/docker/monitoring"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yaml"
ENV_FILE="${HOME}/.config/nova/monitoring.env"

# Load secrets — docker compose --env-file handles the override.
# If monitoring.env doesn't exist, compose uses .env in the compose dir.
export $(grep -v '^#' "$ENV_FILE" 2>/dev/null | xargs) 2>/dev/null || true

exec docker compose -f "$COMPOSE_FILE" --env-file "${ENV_FILE:-/dev/null}" up
