#!/usr/bin/env bash
# One-shot setup to make the Nova/DeerFlow stack reboot-survivable.
#
# What this does (each step is idempotent):
#   1. Drops a systemd unit override on pm2-jahanzaib.service so PM2 only
#      starts after Docker is up. Without this, on a fresh boot PM2 resurrects
#      the `deerflow` app BEFORE dockerd is ready, `docker compose up` fails,
#      and PM2 crash-loops until Docker comes up (was: 923k+ restarts).
#   2. Re-enables pm2-jahanzaib.service so the override takes effect.
#   3. Re-saves the current PM2 process list so a reboot resurrects the
#      llama-bridge + healthcheck + (newly clean) deerflow apps.
#
# Run once: sudo bash setup-reboot-persistence.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: this script must be run with sudo (it touches /etc/systemd/system)" >&2
  exit 1
fi

OVERRIDE_DIR=/etc/systemd/system/pm2-jahanzaib.service.d
OVERRIDE_FILE=$OVERRIDE_DIR/override.conf

mkdir -p "$OVERRIDE_DIR"

cat > "$OVERRIDE_FILE" <<'UNIT_EOF'
[Unit]
# Wait for Docker daemon (and the network it depends on) before PM2 resurrects
# the deerflow app. Without this, `docker compose up` runs before dockerd is
# ready and PM2 crash-loops until Docker comes up.
After=docker.service network-online.target
Wants=docker.service

[Service]
# Explicit DEER_FLOW_ROOT for the deerflow PM2 app's docker-compose invocation.
Environment=DEER_FLOW_ROOT=/home/jahanzaib/Desktop/nova
UNIT_EOF

echo "wrote $OVERRIDE_FILE"
systemctl daemon-reload
echo "systemctl daemon-reload done"

systemctl reenable pm2-jahanzaib.service
echo "reenabled pm2-jahanzaib.service"

# Re-save PM2 state as the jahanzaib user (PM2_HOME is per-user).
if command -v sudo -u jahanzaib >/dev/null 2>&1; then
  sudo -u jahanzaib -H bash -lc 'pm2 save || true'
  echo "pm2 save done (as jahanzaib)"
else
  echo "NOTE: rerun as jahanzaib user: pm2 save"
fi

cat <<'TXT'

Done. Verify with:
  systemctl cat pm2-jahanzaib.service
  pm2 list

To test a reboot end-to-end:
  sudo reboot
  # wait ~90s after login
  pm2 list
TXT
