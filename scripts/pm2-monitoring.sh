#!/bin/sh
# PM2 wrapper for the Nova observability stack (see ecosystem.config.js).
# Runs docker compose in the foreground so PM2 owns the lifecycle.
# All services are loopback-bound — no LAN exposure.
set -eu

COMPOSE_DIR="${DEER_FLOW_ROOT:-$(dirname "$(dirname "$0")")}/docker/monitoring"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yaml"
ENV_FILE="${HOME}/.config/nova/monitoring.env"

# Load secrets — docker compose --env-file handles the override.
# If monitoring.env doesn't exist, compose uses .env in the compose dir.
export $(grep -v '^#' "$ENV_FILE" 2>/dev/null | xargs) 2>/dev/null || true

exec docker compose -f "$COMPOSE_FILE" --env-file "${ENV_FILE:-/dev/null}" up
