#!/usr/bin/env bash
#
# Boot installer for Cloudflare Tunnel enterprise-grade setup.
#
# Idempotent — re-runs are safe. Run as the user that owns PM2 (not root);
# the script uses sudo internally for the operations that need it.
#
# What this installs:
#   1. /etc/cloudflared/config.yml          — tunnel ingress rules
#   2. /etc/cloudflared/env                 — token (mode 0600)
#   3. /etc/systemd/system/cloudflared-nova.service — boot persistence
#   4. /etc/logrotate.d/cloudflared-nova    — log rotation
#   5. /etc/sudoers.d/nova-watchdog         — NOPASSWD for watchdog fixes
#   6. PM2 `nova-tunnel` process            — observability wrapper
#
# Usage:
#   ./install-cloudflared-nova.sh
#
# Env overrides (optional):
#   TUNNEL_TOKEN    — override the Cloudflare tunnel token
#   PUBLIC_URL      — override the public URL the watchdog probes (default
#                     https://nova.alilabsx.com/health)
#
# To rotate the tunnel token:
#   1. Cloudflare dashboard → Zero Trust → Networks → Tunnels →
#      Nova hackathon → Configure → Regenerate token
#   2. Re-run this script with the new TUNNEL_TOKEN env var, or paste
#      into /etc/cloudflared/env manually and:
#         sudo systemctl restart cloudflared-nova
#         pm2 restart nova-tunnel

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUDO="sudo -S -k"

# Pipe sudo password once per invocation (no terminal prompt).
if [ -n "${SUDO_PASSWORD:-}" ]; then
    SUDO="echo ${SUDO_PASSWORD} | sudo -S"
fi

run_sudo() {
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        sudo "$@"
    else
        # Fall back to passwordless sudo; operator can also set $SUDO_PASSWORD.
        "$SUDO" "$@" 2>/dev/null || sudo "$@"
    fi
}

log() { printf '\033[1;32m[boot]\033[0m %s\n' "$*" >&2; }
err() { printf '\033[1;31m[boot]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || err "Run as the user that owns PM2, not root."

# Token: explicit env > existing /etc/cloudflared/env > prompt.
if [ -z "${TUNNEL_TOKEN:-}" ]; then
    if [ -f /etc/cloudflared/env ]; then
        TUNNEL_TOKEN=$(grep '^TUNNEL_TOKEN=' /etc/cloudflared/env | cut -d= -f2-)
    fi
fi
[ -n "${TUNNEL_TOKEN:-}" ] || err "TUNNEL_TOKEN env var required (or set in /etc/cloudflared/env)."

log "1/6 — install /etc/cloudflared/{config.yml,env}"
run_sudo mkdir -p /etc/cloudflared /var/log/cloudflared
run_sudo cp "$REPO_ROOT/config/cloudflared-nova.yml" /etc/cloudflared/config.yml
# Write token atomically with restricted perms.
TEMP_ENV=$(mktemp)
trap 'rm -f "$TEMP_ENV"' EXIT
cat > "$TEMP_ENV" << EOF
# Cloudflare Tunnel token for nova-hackathon.
# Source: Cloudflare dashboard → Zero Trust → Networks → Tunnels →
#         Nova hackathon → Configure → Token.
TUNNEL_TOKEN=$TUNNEL_TOKEN
EOF
run_sudo cp "$TEMP_ENV" /etc/cloudflared/env
run_sudo chmod 0600 /etc/cloudflared/env
run_sudo chown root:root /etc/cloudflared/env /etc/cloudflared/config.yml
rm -f "$TEMP_ENV"
trap - EXIT

log "2/6 — install systemd unit"
run_sudo cp "$REPO_ROOT/config/cloudflared-nova.service" /etc/systemd/system/cloudflared-nova.service
run_sudo chmod 0644 /etc/systemd/system/cloudflared-nova.service
run_sudo systemctl daemon-reload
run_sudo systemctl enable cloudflared-nova.service

log "3/6 — install logrotate config"
run_sudo cp "$REPO_ROOT/config/cloudflared-nova.logrotate" /etc/logrotate.d/cloudflared-nova
run_sudo chmod 0644 /etc/logrotate.d/cloudflared-nova

log "4/6 — install watchdog sudoers (NOPASSWD for tunnel restart)"
TMP_SUDOERS=$(mktemp)
trap 'rm -f "$TMP_SUDOERS"' EXIT
cat > "$TMP_SUDOERS" << 'EOF'
# Allow nova-healthcheck watchdog to manage the nova tunnel systemd unit
# without prompting for a password. The watchdog polls every 30s, so
# password prompts would break the auto-fix path.
#
# Phase C0.1 — added ``reset-failed`` so the watchdog can clear
# start-limit-hit before issuing restart. Without it, restart silently
# fails when systemd has marked the unit failed after too many rapid
# restarts — which is exactly what triggered the Error 1033 outage on
# 2026-07-12 (cloudflared's QUIC connections to edge dropped, the
# binary exited cleanly, systemd's Restart=always tripped 10x in 5 min,
# hit StartLimitBurst, and ``systemctl restart`` from the watchdog
# returned 0 without actually starting the unit).
jahanzaib ALL=(root) NOPASSWD: /usr/bin/systemctl reset-failed cloudflared-nova.service
jahanzaib ALL=(root) NOPASSWD: /usr/bin/systemctl restart cloudflared-nova.service
jahanzaib ALL=(root) NOPASSWD: /usr/bin/systemctl start cloudflared-nova.service
jahanzaib ALL=(root) NOPASSWD: /usr/bin/systemctl stop cloudflared-nova.service
jahanzaib ALL=(root) NOPASSWD: /usr/bin/systemctl status cloudflared-nova.service
jahanzaib ALL=(root) NOPASSWD: /usr/bin/systemctl is-active cloudflared-nova.service
EOF
run_sudo cp "$TMP_SUDOERS" /etc/sudoers.d/nova-watchdog
run_sudo chmod 0440 /etc/sudoers.d/nova-watchdog
run_sudo visudo -c -f /etc/sudoers.d/nova-watchdog
rm -f "$TMP_SUDOERS"
trap - EXIT

log "5/6 — add user to 'adm' group (for journalctl access in pm2 wrapper)"
groups | grep -qw adm || run_sudo usermod -aG adm "$(id -un)" || true
run_sudo chown -R "$(id -un):adm" /var/log/cloudflared

log "6/6 — register PM2 nova-tunnel wrapper"
cd "$REPO_ROOT"
pm2 describe nova-tunnel >/dev/null 2>&1 && pm2 delete nova-tunnel || true
pm2 start ecosystem.config.js --only nova-tunnel
pm2 save

log "all steps complete — restarting tunnel systemd unit"
run_sudo systemctl restart cloudflared-nova.service
sleep 5
state=$(run_sudo systemctl is-active cloudflared-nova.service 2>&1 || true)
log "cloudflared-nova.service: $state"

cat << 'EOF'

Quick reference:
  systemctl status cloudflared-nova    — systemd state
  pm2 logs nova-tunnel                 — heartbeat + tail
  pm2 logs nova-healthcheck --err      — probe failures (P12_tunnel RED → auto-fix)
  journalctl -u cloudflared-nova -f    — raw tunnel journal
  curl https://nova.alilabsx.com/health — public-domain end-to-end check

Reboot persistence: systemd cloudflared-nova.service is enabled.
PM2 daemonized:     pm2 startup + pm2 save (already configured).
Watchdog auto-fix:  P12_tunnel RED for 2 cycles → sudo systemctl restart cloudflared-nova.
EOF