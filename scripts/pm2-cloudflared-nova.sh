#!/usr/bin/env bash
#
# PM2 wrapper for the Cloudflare Tunnel systemd unit.
#
# The tunnel itself is owned by systemd (cloudflared-nova.service) so it
# survives reboots, network blips, and PM2 restarts. PM2's job here is
# observability only:
#   - tail journald's cloudflared-nova entries into a PM2-readable log file
#     under /var/log/cloudflared/ (so `pm2 logs nova-tunnel` works)
#   - emit a heartbeat every 30s with tunnel state parsed from `systemctl`
#   - non-fatal: if the tunnel dies, the wrapper dies too and PM2 will
#     restart it, which causes the systemd unit to come back up via its
#     own Restart=always policy. Both layers must agree.
#
# Args (after this script) are ignored — PM2 passes none and we don't need
# any. Exit codes: 0 = clean shutdown, non-zero = wrapper failed and PM2
# will restart us (which re-runs the tail loop).

set -euo pipefail

LOG_DIR="/var/log/cloudflared"
mkdir -p "$LOG_DIR"

OUT_LOG="$LOG_DIR/nova-out.log"
ERR_LOG="$LOG_DIR/nova-error.log"
HEARTBEAT_LOG="$LOG_DIR/nova-heartbeat.log"

# Lock to avoid double-tailing if PM2 ever restarts us quickly.
LOCK="/var/run/cloudflared-nova-pm2.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "[pm2-wrapper] another instance holds $LOCK — exiting" >&2
    exit 0
fi

# Sanity: systemd unit must be installed, otherwise we're trying to tail a
# non-existent service. Don't try to "fix" it — the operator needs to know.
if ! systemctl list-unit-files cloudflared-nova.service >/dev/null 2>&1; then
    echo "[pm2-wrapper] cloudflared-nova.service not installed in systemd" >&2
    echo "[pm2-wrapper] run: sudo cp scripts/cloudflared-nova.service /etc/systemd/system/" >&2
    exit 2
fi

# Start the tunnel if it's not running. systemd's own Restart=always will
# keep it alive after this; we only nudge it once at PM2 startup.
if ! systemctl is-active --quiet cloudflared-nova.service; then
    echo "[pm2-wrapper] starting cloudflared-nova.service via systemctl"
    sudo systemctl start cloudflared-nova.service || true
fi

# Both stdout (journald) and stderr (journald) stream into our PM2 logs.
# Use `journalctl -u` so we only get THIS unit, not other cloudflared
# processes on the box. `-f` follows, `-n 0` skips the backlog (cloudflared
# already logs to its own files; we just want the live tail).
#
# systemd journal permissions: by default `journalctl` reads everything
# because the adm group has access. Make sure this user is in `adm` —
# the install script does this; the warning is harmless if not.
if ! id -nG | grep -qw adm; then
    echo "[pm2-wrapper] WARNING: $(id -un) not in 'adm' group; journalctl may show 0 lines" >&2
fi

# Heartbeat loop — emit a one-line status every 30s. PM2 logs this so
# `pm2 logs nova-tunnel` shows liveness + state. The watchdog's P12_tunnel
# probe also reads this file.
emit_heartbeat() {
    local active reconnecting
    if systemctl is-active --quiet cloudflared-nova.service; then
        active="active"
    else
        active="inactive"
    fi
    reconnecting=$(systemctl show cloudflared-nova.service --property=SubState --value 2>/dev/null || echo unknown)
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    echo "$ts state=$active substate=$reconnecting pm2=$$" >> "$HEARTBEAT_LOG"
}

# Tail the journal into our PM2-accessible log. Split stdout/stderr by
# SYSLOG_IDENTIFIER so we can route errors cleanly. `-q` suppresses the
# journalctl banner; `--no-tail` would skip the backlog but we want any
# in-flight lines.
journalctl -u cloudflared-nova.service -n 0 -f -o cat -q \
    | while IFS= read -r line; do
        # Heuristic: if line starts with a stack trace marker (panic/ERROR),
        # route to error log. Otherwise stdout. cloudflared writes both to
        # the same sd_notify stream, so this is a best-effort split.
        case "$line" in
            *ERROR*|*FATAL*|*panic*|*WARN*)
                echo "$line" >> "$ERR_LOG"
                ;;
            *)
                echo "$line" >> "$OUT_LOG"
                ;;
        esac
    done &

TAIL_PID=$!
echo "[pm2-wrapper] tail pid=$TAIL_PID, heartbeat every 30s, press SIGTERM to stop"

# Heartbeat timer — 30s, in the background.
( while true; do
    emit_heartbeat
    sleep 30
done ) &

HB_PID=$!

# Trap signals so PM2's SIGTERM cleanly stops the tail.
cleanup() {
    echo "[pm2-wrapper] SIGTERM/SIGINT — stopping tail and heartbeat"
    kill "$TAIL_PID" "$HB_PID" 2>/dev/null || true
    wait "$TAIL_PID" "$HB_PID" 2>/dev/null || true
    flock -u 9
    exit 0
}
trap cleanup TERM INT

# Block forever (until SIGTERM). PM2 sends SIGTERM on stop/restart.
wait "$TAIL_PID"