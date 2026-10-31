#!/bin/sh
# Materialise the secrets the ACP agents overlay mounts (docker/docker-compose.acp.yaml).
#
# OpenClaw's gateway token lives in ~/.openclaw/openclaw.json next to a lot of
# other state. The container must not see that file; it gets exactly the token,
# in a 0600 file under ~/.nova/secrets, mounted read-only at
# /run/nova/openclaw_token. Idempotent; safe to run on every stack start.
set -eu

SRC="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"
DIR="${NOVA_SECRETS_DIR:-$HOME/.nova/secrets}"
OUT="$DIR/openclaw_token"

mkdir -p "$DIR"
chmod 700 "$DIR"
if [ ! -r "$SRC" ]; then
  echo "acp-secrets: $SRC not readable — openclaw agent will not authenticate" >&2
  exit 0
fi
token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["gateway"]["auth"]["token"])' "$SRC" 2>/dev/null || true)"
if [ -z "$token" ]; then
  echo "acp-secrets: no gateway.auth.token in $SRC (auth mode is not token?)" >&2
  exit 0
fi
umask 077
printf '%s' "$token" > "$OUT.tmp"
mv -f "$OUT.tmp" "$OUT"
chmod 600 "$OUT"
echo "acp-secrets: wrote $OUT" >&2
