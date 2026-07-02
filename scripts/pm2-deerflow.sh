#!/usr/bin/env bash
# Wrapper so PM2 can run the deerflow docker-compose stack with the right env
# vars and from the right working directory. PM2 invokes this with
# interpreter:none, so we have to source DEER_FLOW_ROOT and chdir ourselves
# before exec'ing docker compose — `pm2 env` doesn't always propagate to
# subprocesses of docker (which is itself the PM2 "script").
#
# Args (after this script) are passed through verbatim to `docker compose`.
set -euo pipefail

DEER_FLOW_ROOT="${DEER_FLOW_ROOT:-/home/jahanzaib/Desktop/nova}"

# Sanity-check: refuse to run if DEER_FLOW_ROOT doesn't look like the nova
# repo (no docker-compose-dev.yaml). Without this, a stale or wrong env var
# silently chdir's to a wrong directory and docker compose fails with
# confusing "no such file" errors instead of a clear diagnostic.
if [ ! -f "$DEER_FLOW_ROOT/docker/docker-compose-dev.yaml" ]; then
  echo "ERROR: DEER_FLOW_ROOT=$DEER_FLOW_ROOT does not contain docker/docker-compose-dev.yaml" >&2
  echo "       Set DEER_FLOW_ROOT to the nova repo root (e.g. /home/jahanzaib/Desktop/nova)" >&2
  exit 2
fi

export DEER_FLOW_ROOT
cd "$DEER_FLOW_ROOT"

exec /usr/bin/docker compose \
  -f "$DEER_FLOW_ROOT/docker/docker-compose-dev.yaml" \
  -f "$DEER_FLOW_ROOT/docker/docker-compose.dood.yaml" \
  -p deer-flow-dev \
  up --no-build --scale provisioner=0 --scale searxng=0 \
  "$@"
