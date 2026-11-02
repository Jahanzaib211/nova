#!/usr/bin/env bash
# Wrapper so PM2 can run the nova docker-compose stack with the right env
# vars and from the right working directory. PM2 invokes this with
# interpreter:none, so we have to source NOVA_ROOT and chdir ourselves
# before exec'ing docker compose — `pm2 env` doesn't always propagate to
# subprocesses of docker (which is itself the PM2 "script").
#
# Renamed from scripts/pm2-deerflow.sh, which is now a two-line wrapper that
# execs this file (an existing PM2 registration may still point at it).
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
#                  file's header for why it is not an overlay. Same opt-in
#                  overlays as dev (voice on CPU, local LLM, ACP via
#                  docker-compose.nova-prod.acp.yaml).
#
# `prod` requires the images to exist, because this script runs with --no-build:
#   docker compose -f docker/docker-compose.nova-prod.yaml -p nova build
# Switch procedure, rollback and expected downtime: docker/README.md.
#
# ── Names (deer-flow -> nova rename) ────────────────────────────────────────
# Project `nova`, containers `nova-<svc>`, network `nova`, volumes `nova_*`.
# A host whose data still lives in the legacy `deer-flow-dev_*` volumes (i.e.
# scripts/migrate-deerflow-to-nova.sh has not been run) is started under its
# legacy names instead — `-p deer-flow-dev`, NOVA_CONTAINER_PREFIX=deer-flow,
# plus docker-compose.legacy-names.yaml for the old network — so a restart
# before the cutover attaches to the existing database rather than creating an
# empty one. NOVA_COMPOSE_PROJECT pins the project and skips the detection.
set -euo pipefail

NOVA_ROOT="${NOVA_ROOT:-${DEER_FLOW_ROOT:-/home/jahanzaib/Desktop/nova}}"
# PM2 passes only NOVA_ROOT, so `NOVA_STACK=prod` in .env is honoured here (the
# same way NOVA_ACP_AGENTS is below). That makes the switch durable across
# reboots and `pm2 delete/start`, unlike `pm2 restart --update-env`. A value
# already in the process environment wins.
if [ -z "${NOVA_STACK:-}" ]; then
  NOVA_STACK="$(sed -n -E 's/^NOVA_STACK=["]?(dev|prod)["]?[[:space:]]*$/\1/p' "$NOVA_ROOT/.env" 2>/dev/null | tail -1)"
fi
NOVA_STACK="${NOVA_STACK:-dev}"

# Sanity-check: refuse to run if NOVA_ROOT doesn't look like the nova
# repo (no docker-compose-dev.yaml). Without this, a stale or wrong env var
# silently chdir's to a wrong directory and docker compose fails with
# confusing "no such file" errors instead of a clear diagnostic.
if [ ! -f "$NOVA_ROOT/docker/docker-compose-dev.yaml" ]; then
  echo "ERROR: NOVA_ROOT=$NOVA_ROOT does not contain docker/docker-compose-dev.yaml" >&2
  echo "       Set NOVA_ROOT (legacy: DEER_FLOW_ROOT) to the nova repo root (e.g. /home/jahanzaib/Desktop/nova)" >&2
  exit 2
fi

# Both names, same value, for one release: compose files read
# ${NOVA_ROOT:-${DEER_FLOW_ROOT}} and older tooling still reads DEER_FLOW_ROOT.
DEER_FLOW_ROOT="$NOVA_ROOT"
export NOVA_ROOT DEER_FLOW_ROOT
cd "$NOVA_ROOT"

# The project decision below reads docker volumes; with the daemon down every
# lookup "fails" and the host would be misread as fresh. Fail instead and let
# PM2 retry once dockerd answers (boot ordering: setup-reboot-persistence.sh).
if ! /usr/bin/docker info >/dev/null 2>&1; then
  echo "[pm2-nova] docker daemon not reachable yet; exiting so PM2 retries" >&2
  exit 1
fi

# Project/container/network/data-dir names: scripts/lib/nova-names.sh.
# shellcheck source=lib/nova-names.sh
. "$NOVA_ROOT/scripts/lib/nova-names.sh"
DOCKER_BIN=/usr/bin/docker nova_resolve_names "$NOVA_ROOT"
LEGACY_OVERLAY=()
if [ "$NOVA_LEGACY_NAMES" = "1" ]; then
  LEGACY_OVERLAY=(-f "$NOVA_LEGACY_OVERLAY")
  echo "[pm2-nova] legacy names: running as -p $COMPOSE_PROJECT (data still in ${COMPOSE_PROJECT}_* volumes)." >&2
  echo "[pm2-nova]   Migrate with: scripts/migrate-deerflow-to-nova.sh   (dry-run first)" >&2
fi

case "$NOVA_STACK" in
  prod)
    COMPOSE_FILES=(-f "$NOVA_ROOT/docker/docker-compose.nova-prod.yaml")
    ;;
  dev)
    COMPOSE_FILES=(
      -f "$NOVA_ROOT/docker/docker-compose-dev.yaml"
      -f "$NOVA_ROOT/docker/docker-compose.dood.yaml"
      -f "$NOVA_ROOT/docker/docker-compose.prod-frontend.yaml"
    )
    ;;
  *)
    echo "ERROR: NOVA_STACK='$NOVA_STACK' is not valid (expected 'dev' or 'prod')" >&2
    exit 2
    ;;
esac

# ── Opt-in overlays (both stacks) ───────────────────────────────────────────
# The prod stack used to get none of these, so switching to it would have
# silently dropped voice, the local LLM and the ACP agents that the live dev
# stack runs.
_has_gpu_runtime() {
  nvidia-smi -L >/dev/null 2>&1 \
    && /usr/bin/docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'
}

# Voice overlay, same gate as scripts/docker.sh: `speech.enabled: true` in
# config.yaml *and* the weights actually on disk. Without it the gateway
# keeps the host paths from .env, which do not exist inside the container,
# and Kokoro dies at first use with "Voices file not found …" while the
# status probe still claims both engines loaded. This chain — not
# scripts/docker.sh — is what PM2 runs for the live stack, so omitting the
# overlay here meant voice was off in production every time the stack came
# up. Cost a silent voice outage on 2026-08-18.
_voice_dir="${DEERFLOW_VOICE_MODEL_DIR:-$HOME/.cache/nova/voice}"
if sed -n '/^speech:/,/^[^[:space:]#]/p' "$NOVA_ROOT/config.yaml" 2>/dev/null \
     | grep -qE '^[[:space:]]*enabled:[[:space:]]*true' \
   && [ -s "$_voice_dir/voices-v1.0.bin" ] \
   && { [ -s "$_voice_dir/kokoro-v1.0.onnx" ] || [ -s "$_voice_dir/kokoro-v1.0.int8.onnx" ]; }; then
  [ -s "$_voice_dir/kokoro-v1.0.onnx" ] \
    && export DEERFLOW_TTS_MODEL_FILE="kokoro-v1.0.onnx" \
    || export DEERFLOW_TTS_MODEL_FILE="kokoro-v1.0.int8.onnx"
  export DEERFLOW_VOICE_MODEL_DIR="$_voice_dir"
  COMPOSE_FILES+=(-f "$NOVA_ROOT/docker/docker-compose.voice.yaml")
  # GPU voice, only when Docker can actually hand a GPU to a container: a
  # working driver is not enough, the nvidia runtime must be registered, or
  # the device reservation stops the gateway from starting at all.
  # `voice` and `voice-gpu` are alternatives (both provide onnxruntime), and
  # the overlay's UV_EXTRAS replaces the whole list, so every other extra
  # (postgres!) is carried over from DEERFLOW_VOICE_UV_EXTRAS.
  if [ "$NOVA_STACK" = "dev" ] && _has_gpu_runtime; then
    _extras="${DEERFLOW_VOICE_UV_EXTRAS:-$(sed -n 's/^DEERFLOW_VOICE_UV_EXTRAS=//p' "$NOVA_ROOT/.env" | tail -1)}"
    _extras="$(printf '%s' "${_extras:-trading,voice,postgres}" | tr ',' '\n' | grep -vxE 'voice|voice-gpu' | paste -sd, -)"
    export DEERFLOW_VOICE_UV_EXTRAS="${_extras:+$_extras,}voice-gpu"
    COMPOSE_FILES+=(-f "$NOVA_ROOT/docker/docker-compose.voice-gpu.yaml")
    echo "[pm2-nova] GPU voice: extras=$DEERFLOW_VOICE_UV_EXTRAS" >&2
  elif [ "$NOVA_STACK" = "prod" ]; then
    # The prod image bakes its extras at build time (NOVA_PROD_UV_EXTRAS,
    # default CPU `voice`); the GPU path also needs the out-of-band CUDA 12
    # wheels from `make voice-libs`, which no image carries. CPU voice works.
    echo "[pm2-nova] prod stack: voice runs on CPU (GPU voice is dev-stack only)" >&2
  fi
else
  echo "[pm2-nova] voice overlay skipped (speech disabled or weights missing in $_voice_dir)" >&2
fi

# ACP agents (Claude Code + OpenClaw inside Nova): opt-in, because the
# cli-auth overlay exposes ~/.claude to the gateway (see its header).
# PM2 does not pass .env to this wrapper (only NOVA_ROOT), so the
# documented opt-in `NOVA_ACP_AGENTS=1` in .env is honoured here too;
# a value already in the process environment wins.
if [ -z "${NOVA_ACP_AGENTS:-}" ] && grep -qsE '^NOVA_ACP_AGENTS=("1"|1)[[:space:]]*$' "$NOVA_ROOT/.env"; then
  NOVA_ACP_AGENTS=1
fi
# Self-hosted LLM (Ollama on the GPU) for voice turns: opt-in like ACP, and
# only when Docker can hand a GPU to a container — a device reservation
# without the nvidia runtime would stop the whole stack from starting.
if [ -z "${NOVA_LOCAL_LLM:-}" ] && grep -qsE '^NOVA_LOCAL_LLM=("1"|1)[[:space:]]*$' "$NOVA_ROOT/.env"; then
  NOVA_LOCAL_LLM=1
fi
if [ "${NOVA_LOCAL_LLM:-0}" = "1" ] && _has_gpu_runtime; then
  COMPOSE_FILES+=(-f "$NOVA_ROOT/docker/docker-compose.local-llm.yaml")
fi
if [ "${NOVA_ACP_AGENTS:-0}" = "1" ]; then
  # Stages ~/.claude.json and the OpenClaw token into the directories the
  # overlays mount; re-run it by hand after a `claude login`.
  "$NOVA_ROOT/scripts/acp-secrets.sh" || true
  COMPOSE_FILES+=(-f "$NOVA_ROOT/docker/docker-compose.cli-auth.yaml")
  if [ "$NOVA_STACK" = "prod" ]; then
    # uid-1000 variant: /root tmpfs + npm cache in it (see that file's header).
    COMPOSE_FILES+=(-f "$NOVA_ROOT/docker/docker-compose.nova-prod.acp.yaml")
  else
    COMPOSE_FILES+=(-f "$NOVA_ROOT/docker/docker-compose.acp.yaml")
  fi
fi

if [ "$NOVA_STACK" = "dev" ]; then
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
else
  # The prod file defines no provisioner, so `--scale provisioner=0` would
  # abort with "no such service".
  SCALE_FLAGS=()
fi
# The job runner is part of both chains; NOVA_JOBS_SCALE=0 keeps it off on a
# host that does not want background work (it still needs jobs.enabled in
# config.yaml to actually start).
if [ "${NOVA_JOBS_SCALE:-1}" = "0" ]; then
  SCALE_FLAGS+=(--scale jobs=0)
fi

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
  echo "[pm2-nova] WARNING: the drift gate reports frontend_build RED -- the served" >&2
  echo "[pm2-nova]          bundle does not match frontend/src. 'up --no-build' CANNOT" >&2
  echo "[pm2-nova]          fix this. Rebuild the image:" >&2
  echo "[pm2-nova]            docker compose -p $COMPOSE_PROJECT \\" >&2
  echo "[pm2-nova]              -f docker/docker-compose-dev.yaml \\" >&2
  echo "[pm2-nova]              -f docker/docker-compose.dood.yaml \\" >&2
  echo "[pm2-nova]              -f docker/docker-compose.prod-frontend.yaml \\" >&2
  echo "[pm2-nova]              build frontend" >&2
fi

exec /usr/bin/docker compose \
  "${COMPOSE_FILES[@]}" "${LEGACY_OVERLAY[@]}" \
  -p "$COMPOSE_PROJECT" \
  up --no-build "${SCALE_FLAGS[@]}" \
  "$@"
