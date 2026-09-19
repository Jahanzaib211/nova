#!/usr/bin/env bash
# Wrapper so PM2 can run the deerflow docker-compose stack with the right env
# vars and from the right working directory. PM2 invokes this with
# interpreter:none, so we have to source DEER_FLOW_ROOT and chdir ourselves
# before exec'ing docker compose — `pm2 env` doesn't always propagate to
# subprocesses of docker (which is itself the PM2 "script").
#
# Args (after this script) are passed through verbatim to `docker compose`.
#
# ── Stack selection ─────────────────────────────────────────────────────────
# NOVA_STACK=dev   (default) docker-compose-dev.yaml + dood + prod-frontend
#                  overlays. Gateway runs uvicorn --reload and `uv sync` on
#                  every boot.
# NOVA_STACK=prod  docker-compose.nova-prod.yaml — single standalone file, no
#                  reload watcher, venv baked into the image. This is the
#                  intended long-term shape for the public deployment; see that
#                  file's header for why it is not an overlay.
#
# `prod` requires the image to exist, because this script runs with --no-build:
#   docker compose -f docker/docker-compose.nova-prod.yaml -p deer-flow-dev build
# Flip the default only after that build has succeeded and been smoke-tested.
set -euo pipefail

DEER_FLOW_ROOT="${DEER_FLOW_ROOT:-/home/jahanzaib/Desktop/nova}"
NOVA_STACK="${NOVA_STACK:-dev}"

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

case "$NOVA_STACK" in
  prod)
    COMPOSE_FILES=(-f "$DEER_FLOW_ROOT/docker/docker-compose.nova-prod.yaml")
    # This stack defines neither provisioner nor searxng, so the --scale flags
    # the dev chain needs would abort with "no such service".
    SCALE_FLAGS=()
    ;;
  dev)
    COMPOSE_FILES=(
      -f "$DEER_FLOW_ROOT/docker/docker-compose-dev.yaml"
      -f "$DEER_FLOW_ROOT/docker/docker-compose.dood.yaml"
      -f "$DEER_FLOW_ROOT/docker/docker-compose.prod-frontend.yaml"
    )
    # Voice overlay, same gate as scripts/docker.sh: `speech.enabled: true` in
    # config.yaml *and* the weights actually on disk. Without it the gateway
    # keeps the host paths from .env, which do not exist inside the container,
    # and Kokoro dies at first use with "Voices file not found …" while the
    # status probe still claims both engines loaded. This chain — not
    # scripts/docker.sh — is what PM2 runs for the live stack, so omitting the
    # overlay here meant voice was off in production every time the stack came
    # up. Cost a silent voice outage on 2026-08-18.
    _voice_dir="${DEERFLOW_VOICE_MODEL_DIR:-$HOME/.cache/nova/voice}"
    if sed -n '/^speech:/,/^[^[:space:]#]/p' "$DEER_FLOW_ROOT/config.yaml" 2>/dev/null \
         | grep -qE '^[[:space:]]*enabled:[[:space:]]*true' \
       && [ -s "$_voice_dir/voices-v1.0.bin" ] \
       && { [ -s "$_voice_dir/kokoro-v1.0.onnx" ] || [ -s "$_voice_dir/kokoro-v1.0.int8.onnx" ]; }; then
      [ -s "$_voice_dir/kokoro-v1.0.onnx" ] \
        && export DEERFLOW_TTS_MODEL_FILE="kokoro-v1.0.onnx" \
        || export DEERFLOW_TTS_MODEL_FILE="kokoro-v1.0.int8.onnx"
      export DEERFLOW_VOICE_MODEL_DIR="$_voice_dir"
      COMPOSE_FILES+=(-f "$DEER_FLOW_ROOT/docker/docker-compose.voice.yaml")
    else
      echo "[pm2-deerflow] voice overlay skipped (speech disabled or weights missing in $_voice_dir)" >&2
    fi
    # ACP agents (Claude Code + OpenClaw inside Nova): opt-in, because the
    # cli-auth overlay exposes ~/.claude to the gateway (see its header).
    if [ "${NOVA_ACP_AGENTS:-0}" = "1" ]; then
      "$DEER_FLOW_ROOT/scripts/acp-secrets.sh" || true
      COMPOSE_FILES+=(
        -f "$DEER_FLOW_ROOT/docker/docker-compose.cli-auth.yaml"
        -f "$DEER_FLOW_ROOT/docker/docker-compose.acp.yaml"
      )
    fi
    # provisioner stays at 0: nothing in the dev flow provisions sandboxes over
    # HTTP, and nginx already resolves its upstream at request time so the route
    # simply 502s if anyone tries.
    #
    # searxng does NOT stay at 0. config.yaml binds web_search to
    # http://searxng:8080, and that tool falls back to DuckDuckGo *silently* --
    # so scaling it away meant every search quietly used the fallback while the
    # P14 probe and the privacy panel's SearXNG card sat red. Either the service
    # runs or the config should not point at it; it runs.
    SCALE_FLAGS=(--scale provisioner=0)
    # The job runner is part of the chain; NOVA_JOBS_SCALE=0 keeps it off on a
    # host that does not want background work (it still needs jobs.enabled in
    # config.yaml to actually start).
    if [ "${NOVA_JOBS_SCALE:-1}" = "0" ]; then
      SCALE_FLAGS+=(--scale jobs=0)
    fi
    ;;
  *)
    echo "ERROR: NOVA_STACK='$NOVA_STACK' is not valid (expected 'dev' or 'prod')" >&2
    exit 2
    ;;
esac

# ── Stale-frontend warning ────────────────────────────────────────────────
# `up --no-build` can start, restart and recreate the frontend container, but
# it can never rebuild the image -- and the frontend is prod-baked (`next start`
# serves the image's .next) while frontend/src is still bind-mounted over it, so
# a stale build looks live. That combination hid a four-day-old bundle behind a
# current-looking container through every `pm2 restart nova`, while the drift
# gate sat red and unread the whole time.
#
# Warn only, never block: refusing to start the stack over a stale frontend
# would turn a cosmetic drift into an outage. The point is that the operator
# cannot miss it.
_drift="${NOVA_GATE_DRIFT_PATH:-$HOME/.nova/gates/drift.json}"
if [ -r "$_drift" ] && grep -q '"name"[[:space:]]*:[[:space:]]*"frontend_build"' "$_drift" \
   && python3 -c "
import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
sys.exit(0 if any(c.get('name')=='frontend_build' and c.get('status')=='red'
                  for c in d.get('checks',[])) else 1)
" "$_drift" 2>/dev/null; then
  echo "[pm2-deerflow] WARNING: the drift gate reports frontend_build RED -- the served" >&2
  echo "[pm2-deerflow]          bundle does not match frontend/src. 'up --no-build' CANNOT" >&2
  echo "[pm2-deerflow]          fix this. Rebuild the image:" >&2
  echo "[pm2-deerflow]            docker compose -p deer-flow-dev \\" >&2
  echo "[pm2-deerflow]              -f docker/docker-compose-dev.yaml \\" >&2
  echo "[pm2-deerflow]              -f docker/docker-compose.dood.yaml \\" >&2
  echo "[pm2-deerflow]              -f docker/docker-compose.prod-frontend.yaml \\" >&2
  echo "[pm2-deerflow]              build frontend" >&2
fi

exec /usr/bin/docker compose \
  "${COMPOSE_FILES[@]}" \
  -p deer-flow-dev \
  up --no-build "${SCALE_FLAGS[@]}" \
  "$@"
