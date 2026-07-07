#!/usr/bin/env bash
# Stand up an AMD-hosted, OpenAI-compatible LLM endpoint for Nova.
#
# Runs ON the AMD Developer Cloud instance (AMD Instinct MI300X, ROCm). It serves
# a model with vLLM on ROCm and exposes an OpenAI-compatible API that Nova
# registers via the "Add AMD Instinct model (vLLM/ROCm)" preset in
# Settings -> Models. This is Nova's own inference running on AMD silicon — the
# demonstrable AMD-compute story for the Unicorn track.
#
# Unlike scripts/pm2-litellm.sh (a local host service on the docker bridge),
# this endpoint is a REMOTE cloud instance and must be reachable over the
# network, so it binds 0.0.0.0. Because it is public, an API key is REQUIRED and
# you MUST restrict the port to your own IP in the cloud firewall/security group.
set -euo pipefail

MODEL="${AMD_MODEL:-google/gemma-3-27b-it}"   # Gemma → hackathon side-prize eligible
PORT="${AMD_PORT:-8000}"
HOST="${AMD_HOST:-0.0.0.0}"                    # remote instance: must be reachable
TP="${AMD_TP:-1}"                             # tensor-parallel = number of GPUs
IMAGE="${AMD_VLLM_IMAGE:-rocm/vllm:latest}"    # official AMD ROCm vLLM image
API_KEY="${VLLM_API_KEY:-}"

if [ -z "${API_KEY}" ]; then
  echo "WARNING: VLLM_API_KEY is empty — the endpoint will be UNAUTHENTICATED." >&2
  echo "         Set VLLM_API_KEY and lock the port to your IP before exposing it." >&2
fi

# Sanity: confirm we are actually on AMD ROCm hardware before claiming AMD compute.
if command -v rocm-smi >/dev/null 2>&1; then
  echo "== AMD GPUs (rocm-smi) =="
  rocm-smi --showproductname || true
elif command -v rocminfo >/dev/null 2>&1; then
  rocminfo | grep -i "Marketing Name" || true
else
  echo "WARNING: rocm-smi/rocminfo not found — is this an AMD ROCm instance?" >&2
fi

echo "== Serving ${MODEL} via vLLM/ROCm on ${HOST}:${PORT} (TP=${TP}) =="

# --device /dev/kfd + /dev/dri and --group-add video are the ROCm passthroughs.
exec docker run --rm -it \
  --network host \
  --device /dev/kfd --device /dev/dri \
  --group-add video \
  --ipc host \
  --cap-add SYS_PTRACE \
  --security-opt seccomp=unconfined \
  -e HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}" \
  ${API_KEY:+-e VLLM_API_KEY="${API_KEY}"} \
  "${IMAGE}" \
  vllm serve "${MODEL}" \
    --host "${HOST}" \
    --port "${PORT}" \
    --tensor-parallel-size "${TP}" \
    ${API_KEY:+--api-key "${API_KEY}"}
