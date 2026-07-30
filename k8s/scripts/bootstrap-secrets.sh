#!/usr/bin/env bash
# One-time bootstrap of the nova-secrets Secret in the nova-staging namespace.
#
# Mirrors scripts/deploy.sh's generate-once-persist-to-file pattern for
# Compose, but K8s-native: session/auth secrets are freshly generated here
# (never reused from prod's real .env — proper environment isolation between
# a live product and its staging copy), while the real third-party API keys
# ARE pulled from the root .env, since staging needs working LLM calls.
#
# Idempotent: re-running regenerates the Secret (kubectl apply semantics via
# --dry-run|apply), safe to run again after a `helm uninstall`.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root
NAMESPACE="${1:-nova-staging}"
ENV_FILE="${2:-.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found (run from repo root, or pass a path as \$2)" >&2
  exit 1
fi

gen_secret() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null \
    || openssl rand -base64 32 | tr -d '\n'
}

read_env_key() {
  # Reads KEY=value from the .env file, stripping surrounding quotes if any.
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}

kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

# Freshly generated for staging — do not reuse prod's real values.
BETTER_AUTH_SECRET="$(gen_secret)"
DEER_FLOW_INTERNAL_AUTH_TOKEN="$(gen_secret)"
NOVA_OPS_TOKEN="$(gen_secret)"
SEARXNG_SECRET_KEY="$(gen_secret)"

# Real third-party API keys — staging needs these to actually work.
FIREWORKS_API_KEY="$(read_env_key FIREWORKS_API_KEY)"
MINIMAX_API_KEY="$(read_env_key MINIMAX_API_KEY)"
TAVILY_API_KEY="$(read_env_key TAVILY_API_KEY)"
JINA_API_KEY="$(read_env_key JINA_API_KEY)"
SERPER_API_KEY="$(read_env_key SERPER_API_KEY)"
INFOQUEST_API_KEY="$(read_env_key INFOQUEST_API_KEY)"

kubectl create secret generic nova-secrets \
  -n "$NAMESPACE" \
  --from-literal=BETTER_AUTH_SECRET="$BETTER_AUTH_SECRET" \
  --from-literal=DEER_FLOW_INTERNAL_AUTH_TOKEN="$DEER_FLOW_INTERNAL_AUTH_TOKEN" \
  --from-literal=NOVA_OPS_TOKEN="$NOVA_OPS_TOKEN" \
  --from-literal=SEARXNG_SECRET_KEY="$SEARXNG_SECRET_KEY" \
  --from-literal=FIREWORKS_API_KEY="$FIREWORKS_API_KEY" \
  --from-literal=MINIMAX_API_KEY="$MINIMAX_API_KEY" \
  --from-literal=TAVILY_API_KEY="$TAVILY_API_KEY" \
  --from-literal=JINA_API_KEY="$JINA_API_KEY" \
  --from-literal=SERPER_API_KEY="$SERPER_API_KEY" \
  --from-literal=INFOQUEST_API_KEY="$INFOQUEST_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "nova-secrets applied in namespace $NAMESPACE."
