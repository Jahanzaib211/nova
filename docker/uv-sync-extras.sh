#!/usr/bin/env sh
#
# Build-time dependency install for backend/Dockerfile's builder stage.
#
# Extracted from an inline `RUN` for the same reason docker/dev-entrypoint.sh
# was (PR #2767): a multi-line shell loop inside a Dockerfile cannot be written
# safely. Dockerfile `RUN` expands variables it knows (ARG/ENV) and expands
# unknown ones to the empty string, so loop variables like $raw must be escaped
# as \$raw — but the parser then passes the backslash through to /bin/sh, and
# `\$(...)` in command position is a syntax error (sh exits 2 before running
# anything). A script has none of these problems and can be shellcheck'd.
#
# Reads UV_EXTRAS and UV_INDEX_URL from the environment; run with CWD anywhere.
#
# UV_EXTRAS is comma- or whitespace-separated ("trading,voice") — the same
# syntax docker/dev-entrypoint.sh accepts, so a dev container and a prod image
# built from one value get the same dependency set. The previous inline form
# interpolated it straight through as `--extra $UV_EXTRAS`, which turned any
# multi-extra value into the invalid flag `--extra trading,voice`; only
# single-extra builds had ever worked.

set -e

BACKEND_DIR="${1:-/app/backend}"

# ── Resolve extras ──────────────────────────────────────────────────────────
# Validated against [A-Za-z][A-Za-z0-9_-]* so a stray shell metacharacter in a
# build arg cannot reach `uv sync`. Mirrors dev-entrypoint.sh's checks exactly.
EXTRAS_FLAGS=""
if [ -n "${UV_EXTRAS:-}" ]; then
    for raw in $(printf '%s' "$UV_EXTRAS" | tr ',' ' '); do
        [ -z "$raw" ] && continue
        case "$raw" in
            [!A-Za-z]* | *[!A-Za-z0-9_-]*)
                echo "[build] UV_EXTRAS entry '$raw' is invalid (must match [A-Za-z][A-Za-z0-9_-]*) — aborting" >&2
                exit 1
                ;;
        esac
        EXTRAS_FLAGS="$EXTRAS_FLAGS --extra $raw"
    done
fi

if [ -n "$EXTRAS_FLAGS" ]; then
    echo "[build] uv extras:$EXTRAS_FLAGS"
fi

# ── Sync ────────────────────────────────────────────────────────────────────
# `--all-packages` propagates extras into workspace members: deerflow-harness
# declares its own trading / voice / postgres extras, and without this flag they
# are silently skipped (same reason dev-entrypoint.sh uses it — PR #2584).
# $EXTRAS_FLAGS is intentionally unquoted so each `--extra X` becomes its own arg.
cd "$BACKEND_DIR"
# shellcheck disable=SC2086 # word-splitting is intentional here
UV_INDEX_URL="${UV_INDEX_URL:-https://pypi.org/simple}" uv sync --all-packages $EXTRAS_FLAGS
