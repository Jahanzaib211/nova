#!/usr/bin/env bash
# Wrapper so PM2 can run the deerflow docker-compose stack with the right env
# vars and from the right working directory. PM2 invokes this with
# interpreter:none, so we have to source DEER_FLOW_ROOT and chdir ourselves
# before exec'ing docker compose — `pm2 env` doesn't always propagate to
# subprocesses of docker (which is itself the PM2 "script").
#
# Args (after this script) are passed through verbatim to `docker compose`.
set -euo pipefail

export DEER_FLOW_ROOT="${DEER_FLOW_ROOT:-/home/jahanzaib/Desktop/nova}"
cd "$DEER_FLOW_ROOT"

exec /usr/bin/docker compose \
  -f "$DEER_FLOW_ROOT/docker/docker-compose-dev.yaml" \
  -f "$DEER_FLOW_ROOT/docker/docker-compose.dood.yaml" \
  -p deer-flow-dev \
  up --no-build --scale provisioner=0 --scale searxng=0 \
  "$@"
