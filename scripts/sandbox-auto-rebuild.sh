#!/usr/bin/env bash
# Auto-rebuild the sandbox image chain if any layer is missing.
# Intended for cron: runs silently on success, alerts on failure.
#
# Cron entry (every 6 hours):
#   0 */6 * * * /home/jahanzaib/Desktop/nova/scripts/sandbox-auto-rebuild.sh >> /var/log/nova-sandbox-rebuild.log 2>&1
set -euo pipefail

NOVA_ROOT="/home/jahanzaib/Desktop/nova"
SANDBOX_DIR="${NOVA_ROOT}/docker/sandbox"
GATE_DIR="${HOME}/.nova/gates"
LOG="${GATE_DIR}/sandbox-rebuild.log"

mkdir -p "$GATE_DIR"

# Check if all 4 layers exist
REQUIRED_IMAGES=("nova-sandbox-base:latest" "nova-sandbox-tools:latest" "nova-sandbox-dind:latest" "nova-sandbox-android:latest")
MISSING=()
for img in "${REQUIRED_IMAGES[@]}"; do
    if ! docker image inspect "$img" >/dev/null 2>&1; then
        MISSING+=("$img")
    fi
done

if [ ${#MISSING[@]} -eq 0 ]; then
    echo "[$(date -Iseconds)] all sandbox images present — no rebuild needed"
    exit 0
fi

echo "[$(date -Iseconds)] missing sandbox images: ${MISSING[*]}"

# Check vendor integrity
VENDOR="${SANDBOX_DIR}/vendor"
CRITICAL_BINS=(go.tar rust.tar uv kubectl helm terraform nuclei httpx subfinder gitleaks trufflehog dalfox trivy)
VENDOR_OK=true
for bin in "${CRITICAL_BINS[@]}"; do
    if [ ! -f "${VENDOR}/${bin}" ] && [ ! -f "${VENDOR}/${bin}.tar" ]; then
        echo "[$(date -Iseconds)] vendor missing: ${bin}"
        VENDOR_OK=false
        break
    fi
done

if [ "$VENDOR_OK" = false ]; then
    echo "[$(date -Iseconds)] vendor incomplete — cannot auto-rebuild"
    exit 1
fi

# Check disk space
AVAIL_GB=$(df -BG --output=avail / | tail -1 | tr -d ' G')
if [ "$AVAIL_GB" -lt 50 ]; then
    echo "[$(date -Iseconds)] insufficient disk: ${AVAIL_GB}GB free (need 50GB)"
    exit 1
fi

# Trigger rebuild
echo "[$(date -Iseconds)] triggering sandbox image rebuild..."
cd "$SANDBOX_DIR"
if bash build.sh android >> "$LOG" 2>&1; then
    echo "[$(date -Iseconds)] rebuild complete"
    # Update the gate
    python3 "${NOVA_ROOT}/scripts/gates/sandbox-health-gate.py" >> "$LOG" 2>&1 || true
else
    echo "[$(date -Iseconds)] rebuild FAILED — see ${LOG}"
    exit 1
fi
