#!/usr/bin/env sh
#
# DeerFlow gateway dev entrypoint — runs inside the docker-compose-dev gateway
# container. Extracted from docker/docker-compose-dev.yaml's inline `command:`
# (PR #2767, addressing review on Issue #2754).
#
# Responsibilities:
#   1. Resolve `--extra X` flags from UV_EXTRAS (comma- or whitespace-separated,
#      mirroring scripts/detect_uv_extras.py for parity with local `make dev`).
#   2. Validate each extra against [A-Za-z][A-Za-z0-9_-]* so a stray shell
#      metacharacter in `.env` cannot reach `uv sync`.
#   3. `uv sync --all-packages` so workspace member extras (deerflow-harness's
#      postgres extra in particular) are installed — see PR #2584.
#   4. Self-heal: if the first sync fails, recreate .venv and retry once.
#   5. Hand off to uvicorn with reload, replacing this shell so uvicorn becomes
#      PID 1 inside the container.
#
# Anchored at /bin/sh (not bash) since alpine-based base images may not ship
# bash. Uses POSIX-only constructs throughout.

set -e

# `--print-extras` is a dry-run hook: parse + validate UV_EXTRAS, print the
# resulting `--extra X` flags to stdout, and exit. Used by the unit test in
# backend/tests/test_dev_entrypoint.py and useful for ad-hoc debugging.
PRINT_EXTRAS_ONLY=0
if [ "${1:-}" = "--print-extras" ]; then
    PRINT_EXTRAS_ONLY=1
fi

# Mirror the legacy command's behavior: redirect both stdout and stderr to the
# host-mounted log file (../logs/gateway.log → /app/logs/gateway.log). Skip
# the redirect under --print-extras so the test runner can capture stdout.
if [ "$PRINT_EXTRAS_ONLY" = "0" ]; then
    exec >/app/logs/gateway.log 2>&1
fi

# ── Resolve extras ──────────────────────────────────────────────────────────

EXTRAS_FLAGS=""
if [ -n "${UV_EXTRAS:-}" ]; then
    # Normalize comma → space, then split on whitespace via the unquoted `for`.
    for raw in $(printf '%s' "$UV_EXTRAS" | tr ',' ' '); do
        [ -z "$raw" ] && continue
        # Reject anything that does not look like an identifier.
        # Two patterns: leading non-letter, or any non-[A-Za-z0-9_-] character.
        case "$raw" in
            [!A-Za-z]* | *[!A-Za-z0-9_-]*)
                echo "[startup] UV_EXTRAS entry '$raw' is invalid (must match [A-Za-z][A-Za-z0-9_-]*) — aborting" >&2
                exit 1
                ;;
        esac
        EXTRAS_FLAGS="$EXTRAS_FLAGS --extra $raw"
    done
fi

if [ "$PRINT_EXTRAS_ONLY" = "1" ]; then
    # Trim leading space for tidier output, then exit.
    printf '%s\n' "${EXTRAS_FLAGS# }"
    exit 0
fi

if [ -n "$EXTRAS_FLAGS" ]; then
    echo "[startup] uv extras:$EXTRAS_FLAGS"
fi

# Keep runtime-owned files out of uvicorn's reload watcher. Each excluded path
# must exist before uvicorn starts so watchfiles treats it as an excluded
# directory, not as a plain glob pattern — on Python 3.12, globbing an absolute
# pattern raises NotImplementedError and crashes startup (#3459 / #3454). That
# means `sandbox` must be created here too, not just `.deer-flow`.
: "${DEER_FLOW_HOME:=/app/backend/.deer-flow}"
export DEER_FLOW_HOME
mkdir -p "$DEER_FLOW_HOME" /app/backend/.deer-flow /app/backend/sandbox /app/backend/tests

# ── Sync dependencies (with self-heal) ──────────────────────────────────────

cd /app/backend

# `--all-packages` propagates extras into workspace members (PR #2584).
# `$EXTRAS_FLAGS` intentionally unquoted so each `--extra X` becomes its own arg.
# shellcheck disable=SC2086 # word-splitting is intentional here
if ! uv sync --all-packages $EXTRAS_FLAGS; then
    echo "[startup] uv sync failed; recreating .venv and retrying once"
    uv venv --allow-existing .venv
    # shellcheck disable=SC2086
    uv sync --all-packages $EXTRAS_FLAGS
fi

# ── Voice weights sanity check ──────────────────────────────────────────────
#
# The `voice` extra installs fine without the weights, so both engines report
# as "loaded" and the failure only surfaces at first use, as
#   Voices file not found at /home/<host-user>/.cache/nova/voice/voices-v1.0.bin
# — a host path that does not exist inside this container. That happens
# whenever the gateway is started without docker-compose.voice.yaml (e.g. a
# bare `docker compose up -d --force-recreate gateway` to recover a broken
# container), because the overlay is what bind-mounts the weights to
# /app/voice-models and rewrites these paths. Cost a silent voice outage on
# 2026-08-18. Warn loudly at boot instead of at first use; do not exit, since
# a gateway without voice is still a working gateway.
case "${UV_EXTRAS:-}" in
    *voice*)
        for _vw in "${DEERFLOW_TTS_VOICES_PATH:-}" "${DEERFLOW_TTS_MODEL_PATH:-}"; do
            if [ -n "$_vw" ] && [ ! -s "$_vw" ]; then
                echo "[startup] ⚠ voice extra is installed but '$_vw' is missing inside the container." >&2
                echo "[startup]   The voice overlay was not applied. Restart with:" >&2
                echo "[startup]     ./scripts/docker.sh start" >&2
                echo "[startup]   or add -f docker/docker-compose.voice.yaml to your compose command." >&2
            fi
        done
        unset _vw
        ;;
esac

# ── Hand off to uvicorn ─────────────────────────────────────────────────────

PYTHONPATH=. exec uv run uvicorn app.gateway.app:app \
    --host 0.0.0.0 --port 8001 \
    --reload \
    --reload-include='*.yaml' \
    --reload-include='.env' \
    --reload-exclude=/app/backend/sandbox \
    --reload-exclude="$DEER_FLOW_HOME" \
    --reload-exclude=/app/backend/.deer-flow \
    --reload-exclude=/app/backend/tests \
    --timeout-graceful-shutdown 10
    # Without a graceful-shutdown bound, a reload waits forever for the UI's
    # long-lived SSE connections to close — the old worker never exits and
    # code changes silently never land (the "wedged reloader").
