# shellcheck shell=bash
# Shared naming resolution for the dev/prod compose launchers (sourced by
# scripts/pm2-nova.sh and scripts/docker.sh; not executable on its own).
#
# The stack was renamed deer-flow -> nova: project `deer-flow-dev` -> `nova`,
# containers `deer-flow-<svc>` -> `nova-<svc>`, network -> `nova`, volumes
# `deer-flow-dev_*` -> `nova_*`. scripts/migrate-deerflow-to-nova.sh performs
# the operator-run cutover. Until a host has been through it, its data lives in
# the legacy volumes, and starting the stack as `-p nova` would come up on a
# brand-new EMPTY database. So:
#
#   NOVA_COMPOSE_PROJECT set          -> use it verbatim
#   volume nova_nova-postgres-data    -> nova            (migrated, or fresh+started)
#   volume deer-flow-dev_nova-...     -> deer-flow-dev   (legacy names, not migrated)
#   neither                           -> nova            (fresh host)
#
# Legacy mode also sets NOVA_CONTAINER_PREFIX=deer-flow and adds
# docker/docker-compose.legacy-names.yaml (attaches the old network as
# external). Callers must ensure the docker daemon answers first: with it down
# every lookup fails and the host would be misread as fresh.
#
# Sets/exports: COMPOSE_PROJECT, NOVA_COMPOSE_PROJECT, NOVA_LEGACY_NAMES (0/1),
# NOVA_LEGACY_OVERLAY (path or empty), NOVA_CONTAINER_PREFIX (legacy only),
# NOVA_GATEWAY_IMAGE, NOVA_DATA_DIRNAME.

NOVA_LEGACY_PROJECT="deer-flow-dev"

nova_resolve_names() {
  local root="$1"
  local docker_bin="${DOCKER_BIN:-docker}"
  if [ -n "${NOVA_COMPOSE_PROJECT:-}" ]; then
    COMPOSE_PROJECT="$NOVA_COMPOSE_PROJECT"
  elif "$docker_bin" volume inspect nova_nova-postgres-data >/dev/null 2>&1; then
    COMPOSE_PROJECT="nova"
  elif "$docker_bin" volume inspect "${NOVA_LEGACY_PROJECT}_nova-postgres-data" >/dev/null 2>&1; then
    COMPOSE_PROJECT="$NOVA_LEGACY_PROJECT"
  else
    COMPOSE_PROJECT="nova"
  fi
  NOVA_LEGACY_NAMES=0
  NOVA_LEGACY_OVERLAY=""
  if [ "$COMPOSE_PROJECT" = "$NOVA_LEGACY_PROJECT" ]; then
    NOVA_LEGACY_NAMES=1
    NOVA_LEGACY_OVERLAY="$root/docker/docker-compose.legacy-names.yaml"
    export NOVA_CONTAINER_PREFIX="${NOVA_CONTAINER_PREFIX:-deer-flow}"
  fi
  NOVA_COMPOSE_PROJECT="$COMPOSE_PROJECT"
  # The jobs service reuses the image the gateway service builds, which compose
  # names <project>-gateway.
  NOVA_GATEWAY_IMAGE="${NOVA_GATEWAY_IMAGE:-${COMPOSE_PROJECT}-gateway:latest}"
  # Runtime data dir under backend/: .nova, or the legacy .deer-flow while it is
  # the only one (after the migration .deer-flow is a symlink to .nova).
  if [ -z "${NOVA_DATA_DIRNAME:-}" ]; then
    if [ -e "$root/backend/.nova" ] || [ ! -e "$root/backend/.deer-flow" ]; then
      NOVA_DATA_DIRNAME=".nova"
    else
      NOVA_DATA_DIRNAME=".deer-flow"
    fi
  fi
  export COMPOSE_PROJECT NOVA_COMPOSE_PROJECT NOVA_LEGACY_NAMES NOVA_LEGACY_OVERLAY NOVA_GATEWAY_IMAGE NOVA_DATA_DIRNAME
}
