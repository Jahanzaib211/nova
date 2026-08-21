#!/usr/bin/env bash
# Build Nova's sandbox image chain, reusing this machine's toolchains.
#
# The chain is four layers, each one concern:
#
#   base    pinned upstream digest — the thing that makes builds reproducible
#   tools   pandoc, psql, Go, Rust, Playwright, uv, …
#   dind    a Docker daemon for the agent (needs sandbox.privileged to be used)
#   android JDK / Kotlin / Gradle
#
# Chained tags share layers, so the intermediate tags cost essentially no disk
# and let you point config.yaml's `sandbox.image` at whichever rung you want.
#
# Before building, this stages host toolchains into vendor/ so the image copies
# them instead of downloading them. Only binaries verified to run against the
# image's glibc 2.35 are staged — see Dockerfile.tools for the full reasoning.
# Anything missing here is simply downloaded by the Dockerfile instead, so a
# clean checkout on another machine still builds.
set -euo pipefail

readonly HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly VENDOR="${HERE}/vendor"
TARGET="${1:-android}"

log() { printf '  %s\n' "$*"; }

stage_vendor() {
    rm -rf "$VENDOR"
    mkdir -p "$VENDOR"
    : >"${VENDOR}/.keep"

    # Go — statically linked, so the whole GOROOT transplants as-is.
    local goroot
    if goroot="$(go env GOROOT 2>/dev/null)" && [ -d "$goroot" ]; then
        tar -cf "${VENDOR}/go.tar" -C "$(dirname "$goroot")" "$(basename "$goroot")"
        log "staged go from ${goroot} ($(go version | awk '{print $3}'))"
    else
        log "go not on host — Dockerfile will download it"
    fi

    # Rust — rustup toolchains are built against glibc 2.17 on purpose.
    local rust
    rust="$(ls -d "${HOME}"/.rustup/toolchains/*-x86_64-unknown-linux-gnu 2>/dev/null | head -1 || true)"
    if [ -n "$rust" ] && [ -d "$rust" ]; then
        tar -cf "${VENDOR}/rust.tar" -C "$(dirname "$rust")" "$(basename "$rust")"
        log "staged rust from ${rust}"
    else
        log "rust not on host — Dockerfile will download it"
    fi

    # uv — single binary, glibc 2.17.
    if command -v uv >/dev/null 2>&1; then
        cp "$(command -v uv)" "${VENDOR}/uv"
        log "staged uv $(uv --version | awk '{print $2}')"
    else
        log "uv not on host — Dockerfile will download it"
    fi

    # Docker engine — every piece except runc, whose host build needs glibc 2.38.
    local need=(dockerd containerd docker docker-proxy containerd-shim-runc-v2)
    local have=() missing=()
    for bin in "${need[@]}"; do
        if command -v "$bin" >/dev/null 2>&1; then have+=("$(command -v "$bin")"); else missing+=("$bin"); fi
    done
    if [ ${#missing[@]} -eq 0 ]; then
        mkdir -p "${VENDOR}/docker"
        cp "${have[@]}" "${VENDOR}/docker/"
        log "staged docker engine (${#have[@]} binaries; runc is downloaded — host build needs glibc 2.38)"
    else
        log "docker engine incomplete on host (missing: ${missing[*]}) — Dockerfile will download it"
    fi
}

build_layer() {
    local name="$1" dockerfile="$2" base="${3:-}"
    local args=(-t "nova-sandbox-${name}:latest" -f "${HERE}/${dockerfile}")
    [ -n "$base" ] && args+=(--build-arg "BASE=${base}")
    log "building nova-sandbox-${name} …"
    docker build "${args[@]}" "$HERE"
}

echo "Staging host toolchains into vendor/"
stage_vendor
echo
echo "Building chain up to: ${TARGET}"

build_layer base Dockerfile.base
[ "$TARGET" = "base" ] && exit 0

build_layer tools Dockerfile.tools nova-sandbox-base:latest
[ "$TARGET" = "tools" ] && exit 0

build_layer dind Dockerfile.dind nova-sandbox-tools:latest
[ "$TARGET" = "dind" ] && exit 0

build_layer android Dockerfile.android nova-sandbox-dind:latest

echo
log "done — point config.yaml's sandbox.image at nova-sandbox-${TARGET}:latest"
