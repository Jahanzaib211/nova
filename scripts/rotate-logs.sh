#!/usr/bin/env bash
#
# Rotate Nova's host-side logs.
#
# Why this exists: docker/dev-entrypoint.sh appends to logs/gateway.log rather
# than truncating, because truncate-on-boot destroyed the evidence of the very
# crash that caused the boot (the 2026-08-19 degradation). Appending is only
# safe if something else bounds the size — that something is this script.
#
# Rotation is size-triggered, not time-triggered: a crash-loop can write more in
# ten minutes than a healthy week, and a daily rotation would let it fill the
# disk. Rotated generations are gzipped; the newest MAX_GEN are kept.
#
# The nginx visitor log is deliberately NOT rotated here. It is a durable
# analytics store with its own retention tool that understands the JSONL
# record format:  scripts/nova-visitors.py --prune <days>
#
# Usage:
#   scripts/rotate-logs.sh              # rotate anything over the size cap
#   scripts/rotate-logs.sh --force      # rotate regardless of size
#   scripts/rotate-logs.sh --json       # machine-readable summary (gate feed)
#
# Exit codes: 0 always, unless a rotation actually failed (1). A log that is
# simply under the cap is a success, not a no-op failure.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Rotate when a file exceeds this many bytes (default 64 MiB).
MAX_BYTES="${NOVA_LOG_MAX_BYTES:-67108864}"
# Keep this many compressed generations per log.
MAX_GEN="${NOVA_LOG_MAX_GENERATIONS:-7}"

FORCE=0
JSON=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --json)  JSON=1 ;;
        -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

TARGETS=(
    "$REPO_ROOT/logs/gateway.log"
    "$REPO_ROOT/logs/frontend.log"
)

failures=0
results=()

for log in "${TARGETS[@]}"; do
    name="$(basename "$log")"

    if [ ! -f "$log" ]; then
        results+=("{\"log\":\"$name\",\"action\":\"absent\",\"bytes\":0}")
        continue
    fi

    size="$(stat -c %s "$log" 2>/dev/null || echo 0)"

    if [ "$FORCE" -eq 0 ] && [ "$size" -lt "$MAX_BYTES" ]; then
        results+=("{\"log\":\"$name\",\"action\":\"under_cap\",\"bytes\":$size}")
        continue
    fi

    # Pre-flight: a log whose first byte is NUL was truncated while its writer
    # held a non-append (plain `>`) fd. The writer kept its old offset and
    # re-created the file as a sparse hole. Rotating again would just repeat
    # that, so treat it as a fault: compact the file down to its real content
    # so the disk is reclaimed, then fail the run so the gate goes RED until
    # the container is restarted onto the current dev-entrypoint.sh.
    if [ "$size" -gt 0 ] && [ "$(head -c 1 "$log" | tr -d '\0' | wc -c)" -eq 0 ]; then
        real="$(tr -d '\0' < "$log" | wc -c)"
        tr -d '\0' < "$log" > "${log}.compact" && mv "${log}.compact" "$log"
        echo "rotate-logs: $name is a sparse NUL hole (${size}B on disk, ${real}B real)." >&2
        echo "rotate-logs:   Its writer does not hold an O_APPEND fd, so this container" >&2
        echo "rotate-logs:   predates the append fix in docker/dev-entrypoint.sh." >&2
        echo "rotate-logs:   Compacted in place. Restart the stack to fix permanently:" >&2
        echo "rotate-logs:     NOVA_STACK=dev pm2 restart nova" >&2
        failures=$((failures + 1))
        results+=("{\"log\":\"$name\",\"action\":\"sparse_writer_compacted\",\"bytes\":$size,\"real_bytes\":$real}")
        continue
    fi

    stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
    rotated="${log}.${stamp}"

    # Copy-then-truncate rather than mv: the writer (uvicorn inside the
    # container) holds an open fd on this inode. A rename would leave it
    # writing to the rotated file forever and the live log permanently empty.
    #
    # Truncating in place is only safe because dev-entrypoint.sh opens the log
    # with `>>` (O_APPEND). An O_APPEND fd seeks to end-of-file before every
    # write, so after truncation the next write lands at offset 0. With a plain
    # `>` fd the writer would keep its old offset and recreate the file as a
    # sparse hole of nulls the same size as before — the standard copytruncate
    # trap. The append mode and this rotation are a matched pair; changing
    # either one alone breaks the other.
    if cp "$log" "$rotated" && : > "$log"; then
        gzip -f "$rotated"
        results+=("{\"log\":\"$name\",\"action\":\"rotated\",\"bytes\":$size,\"archive\":\"$(basename "$rotated").gz\"}")
    else
        echo "rotate-logs: FAILED to rotate $log" >&2
        failures=$((failures + 1))
        results+=("{\"log\":\"$name\",\"action\":\"failed\",\"bytes\":$size}")
        continue
    fi

    # Prune old generations, newest first, keeping MAX_GEN.
    mapfile -t old < <(ls -1t "${log}."*.gz 2>/dev/null | tail -n "+$((MAX_GEN + 1))")
    for stale in "${old[@]:-}"; do
        [ -n "$stale" ] && rm -f "$stale"
    done
done

if [ "$JSON" -eq 1 ]; then
    printf '{"checked_at_epoch":%s,"max_bytes":%s,"max_generations":%s,"logs":[%s]}\n' \
        "$(date +%s)" "$MAX_BYTES" "$MAX_GEN" \
        "$(IFS=,; echo "${results[*]}")"
else
    for r in "${results[@]}"; do echo "$r"; done
fi

[ "$failures" -eq 0 ]
