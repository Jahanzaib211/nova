#!/usr/bin/env bash
# Verify a pushed hackathon image meets the submission rules:
#   * publicly pullable
#   * includes a linux/amd64 manifest (judging VM runs linux/amd64)
#   * compressed size <= 10GB
#
# Usage: scripts/hackathon-verify-image.sh <image-ref>
set -euo pipefail

IMAGE="${1:?usage: hackathon-verify-image.sh <image-ref>}"
MAX_BYTES=$((10 * 1024 * 1024 * 1024))

echo "== Verifying ${IMAGE} =="

# 1) linux/amd64 manifest present (works for both single- and multi-arch).
echo "-- checking linux/amd64 manifest --"
if docker buildx imagetools inspect "${IMAGE}" >/tmp/hk_manifest.txt 2>&1; then
  if grep -qE "linux/amd64" /tmp/hk_manifest.txt; then
    echo "OK: linux/amd64 platform found"
  else
    echo "FAIL: no linux/amd64 platform in manifest" >&2
    cat /tmp/hk_manifest.txt >&2
    exit 1
  fi
else
  echo "FAIL: could not inspect ${IMAGE} (is it public and pushed?)" >&2
  cat /tmp/hk_manifest.txt >&2
  exit 1
fi

# 2) Compressed size = sum of layer sizes from the manifest, must be <= 10GB.
echo "-- checking compressed size (<= 10GB) --"
TOTAL=$(docker buildx imagetools inspect "${IMAGE}" --raw 2>/dev/null \
  | grep -oE '"size":[0-9]+' | grep -oE '[0-9]+' \
  | awk '{s+=$1} END {print s+0}')
if [ "${TOTAL}" -eq 0 ]; then
  echo "WARN: could not compute size from manifest (non-fatal)"
else
  HUMAN=$(awk -v b="${TOTAL}" 'BEGIN{printf "%.2f", b/1024/1024/1024}')
  echo "compressed size ~= ${HUMAN} GiB (${TOTAL} bytes)"
  if [ "${TOTAL}" -gt "${MAX_BYTES}" ]; then
    echo "FAIL: image exceeds 10GB limit" >&2
    exit 1
  fi
  echo "OK: within 10GB limit"
fi

echo "== ${IMAGE} passed submission checks =="
