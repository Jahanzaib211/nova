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

readonly WHEELS="${VENDOR}/wheels"

# Python wheels pinned here rather than in the Dockerfile so the host download
# and the in-image install agree on one list.
readonly PY_PACKAGES=(playwright pytest ruff mypy)

stage_wheels() {
    # PyPI is the one slow dependency in this build. Measured on this host:
    # Ubuntu archive 5.6 MB/s, Cloudflare 3.7 MB/s, files.pythonhosted.org
    # (Fastly) 0.07 MB/s — about 50x slower, so four wheels took 17 minutes
    # while the entire apt layer took 162 seconds. That is a route between this
    # ISP and Fastly, not something a Dockerfile can fix.
    #
    # So the wheels are cached on disk and survive everything that would
    # otherwise force a re-download: a Dockerfile edit, `docker builder prune`,
    # a fresh layer. Downloaded once, reused forever. Deliberately NOT wiped
    # with the rest of vendor/.
    mkdir -p "$WHEELS"
    if compgen -G "${WHEELS}/*.whl" >/dev/null; then
        log "wheels: reusing $(ls "${WHEELS}"/*.whl | wc -l) cached (skipping PyPI)"
        return
    fi
    log "wheels: cache empty — downloading once from PyPI (slow here; cached after this)"
    # Platform pinned to the image, not this host: the host is glibc 2.43 and
    # the image is 2.35, so an unconstrained download can pick a wheel the
    # image cannot load.
    # /usr/bin/python3 explicitly: a bare `python3` here resolves to whatever
    # venv is active (Nova's is uv-managed and ships no pip), which fails with
    # a "No module named pip" that has nothing to do with the download.
    #
    # All three manylinux tags, because they are not interchangeable and the
    # set in use varies per project: playwright publishes manylinux1_x86_64,
    # ruff and mypy publish manylinux_2_17/2014. Listing only the newer two
    # silently skipped playwright and failed the whole batch.
    if /usr/bin/python3 -m pip download \
            --only-binary=:all: \
            --python-version 3.12 \
            --platform manylinux1_x86_64 \
            --platform manylinux2014_x86_64 \
            --platform manylinux_2_17_x86_64 \
            --dest "$WHEELS" \
            "${PY_PACKAGES[@]}" >"${VENDOR}/wheels-download.log" 2>&1; then
        log "wheels: cached $(ls "${WHEELS}"/*.whl 2>/dev/null | wc -l) for future builds"
    else
        log "wheels: host download failed (see vendor/wheels-download.log) — the image will fetch from PyPI instead"
        rm -f "${WHEELS}"/*.whl 2>/dev/null || true
    fi
}

stage_android() {
    # Gradle, Kotlin and the Android SDK are ~700 MB of downloads from
    # services.gradle.org and dl.google.com. services.gradle.org currently
    # answers HTTP 200 and then transfers nothing from this network — the same
    # stalled-CDN shape as PyPI — so the layer hangs forever rather than failing.
    #
    # A previously built android image on this machine already contains all
    # three, so lift them out of it. Same rule as every other toolchain here:
    # reuse what the machine has, download only what it lacks. The Dockerfile
    # keeps the download path for a host with no donor image.
    local donor="${NOVA_ANDROID_DONOR:-nova-sandbox-android:preserved-20260821}"
    if ! docker image inspect "$donor" >/dev/null 2>&1; then
        log "android: no donor image ($donor) — Dockerfile will download the SDK"
        return
    fi
    if [ -d "${VENDOR}/android/gradle" ]; then
        log "android: reusing already-staged SDK/Gradle/Kotlin"
        return
    fi
    log "android: extracting Gradle/Kotlin/SDK from ${donor} ..."
    mkdir -p "${VENDOR}/android"
    local cid
    cid=$(docker create "$donor" 2>/dev/null) || { log "android: could not create donor container"; return; }
    for d in gradle kotlin android-sdk; do
        docker cp "${cid}:/opt/${d}" "${VENDOR}/android/${d}" >/dev/null 2>&1 || log "android: /opt/${d} not in donor"
    done
    docker rm -f "$cid" >/dev/null 2>&1 || true
    log "android: staged $(du -sh "${VENDOR}/android" 2>/dev/null | cut -f1) from the donor image"
}

stage_vendor() {
    # Preserve the wheel cache; wipe everything else.
    # Both caches are expensive to rebuild and cheap to keep, so they survive
    # the wipe: wheels because PyPI is ~50x slower than any other CDN here, and
    # android because services.gradle.org stalls outright.
    local keep=""
    keep="$(mktemp -d)"
    [ -d "$WHEELS" ] && mv "$WHEELS" "${keep}/wheels"
    [ -d "${VENDOR}/android" ] && mv "${VENDOR}/android" "${keep}/android"
    # Preserve vendored go-bins and kubectl/helm/terraform — large binaries
    # that are expensive to download on this ISP (GitHub CDN throttling).
    local gobins=(nuclei httpx subfinder gitleaks trufflehog dalfox trivy kubectl helm terraform)
    for b in "${gobins[@]}"; do
        [ -f "${VENDOR}/${b}" ] && mv "${VENDOR}/${b}" "${keep}/${b}"
    done
    rm -rf "$VENDOR"
    mkdir -p "$VENDOR"
    : >"${VENDOR}/.keep"
    [ -d "${keep}/wheels" ] && mv "${keep}/wheels" "$WHEELS"
    [ -d "${keep}/android" ] && mv "${keep}/android" "${VENDOR}/android"
    for b in "${gobins[@]}"; do
        [ -f "${keep}/${b}" ] && mv "${keep}/${b}" "${VENDOR}/${b}"
    done
    rm -rf "$keep"

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
    # docker-init lives in libexec, off PATH, so the loop above never finds it.
    # Without it the daemon logs "Failed to find docker-init" and `docker run
    # --init` cannot reap zombies in inner containers.
    for candidate in /usr/libexec/docker/docker-init /usr/bin/docker-init; do
        [ -x "$candidate" ] && { have+=("$candidate"); break; }
    done
    if [ ${#missing[@]} -eq 0 ]; then
        mkdir -p "${VENDOR}/docker"
        cp "${have[@]}" "${VENDOR}/docker/"
        log "staged docker engine (${#have[@]} binaries; runc is downloaded — host build needs glibc 2.38)"
    else
        log "docker engine incomplete on host (missing: ${missing[*]}) — Dockerfile will download it"
    fi
}

# A build id every layer carries, so an agent inside the container can answer
# "which image am I" without reconstructing it from /var/log/apt/history.log --
# which is what actually happened, twice, and produced the wrong answer both
# times. Commit sha plus a UTC timestamp: the sha says what code built it, the
# timestamp distinguishes two builds of the same commit.
NOVA_BUILD_ID="$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || echo nogit)-$(date -u +%Y%m%dT%H%M%SZ)"
readonly NOVA_BUILD_ID

# The digest Dockerfile.base pins, read from the file rather than duplicated
# here, so the manifest cannot claim a different upstream than the build used.
NOVA_UPSTREAM_DIGEST="$(grep -oE 'all-in-one-sandbox@sha256:[0-9a-f]{64}' "${HERE}/Dockerfile.base" | head -1 | cut -d@ -f2)"
readonly NOVA_UPSTREAM_DIGEST

build_layer() {
    local name="$1" dockerfile="$2" base="${3:-}"
    local args=(-t "nova-sandbox-${name}:latest" -f "${HERE}/${dockerfile}")
    [ -n "$base" ] && args+=(--build-arg "BASE=${base}")
    args+=(--build-arg "NOVA_BUILD_ID=${NOVA_BUILD_ID}")
    args+=(--build-arg "NOVA_UPSTREAM_DIGEST=${NOVA_UPSTREAM_DIGEST}")
    log "building nova-sandbox-${name} …"
    docker build "${args[@]}" "$HERE"
}

echo "Staging host toolchains into vendor/"
stage_vendor
stage_wheels
stage_android
echo
echo "Building chain up to: ${TARGET}"
log "build id: ${NOVA_BUILD_ID}"

build_layer base Dockerfile.base
[ "$TARGET" = "base" ] && exit 0

build_layer tools Dockerfile.tools nova-sandbox-base:latest
[ "$TARGET" = "tools" ] && exit 0

build_layer dind Dockerfile.dind nova-sandbox-tools:latest
[ "$TARGET" = "dind" ] && exit 0

build_layer android Dockerfile.android nova-sandbox-dind:latest

echo
log "done — point config.yaml's sandbox.image at nova-sandbox-${TARGET}:latest"
