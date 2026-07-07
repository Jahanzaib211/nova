#!/bin/sh
# PM2 wrapper for the nova-litellm proxy (see ecosystem.config.js).
#
# Binds the docker bridge IP only — Nova's gateway container reaches it via
# host.docker.internal:4000; nothing on the LAN can. Absolute venv path keeps
# pm2 resurrection working regardless of shell PATH or snap remaps.
set -eu

LITELLM_BIN="${NOVA_LITELLM_BIN:-/home/jahanzaib/.nova-litellm/bin/litellm}"
CONFIG="${NOVA_LITELLM_CONFIG:-/home/jahanzaib/Desktop/nova/docker/litellm/config.yaml}"
HOST="${NOVA_LITELLM_HOST:-172.17.0.1}"
PORT="${NOVA_LITELLM_PORT:-4000}"

case "$HOST" in
  0.0.0.0|::) echo "refusing to bind $HOST — LAN exposure" >&2; exit 2 ;;
esac

exec "$LITELLM_BIN" --config "$CONFIG" --host "$HOST" --port "$PORT"
