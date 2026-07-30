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
#
# Phase 6 (k8s/ARCHITECTURE.md §3's confirmed gap): the original version of
# this script only bootstrapped 10 of the secrets Compose's `env_file: ../.env`
# pattern implicitly grants — Stripe, BYOK, IM channel credentials, most
# LLM/search providers, and DATABASE_URL were silently absent from the K8s
# deployment. Every secret-shaped env var this codebase actually reads
# (found via `grep -rn os.environ` across app/ and packages/harness/, plus
# every $VAR reference in config.example.yaml) is now covered explicitly —
# deliberately NOT a blind full-.env passthrough, since .env also carries
# local-machine path/config values (DEER_FLOW_HOME, DEER_FLOW_CONFIG_PATH,
# etc.) that would be wrong inside a container and would silently win over
# configmap-env.yaml's correct K8s-specific values (secretRef is listed
# after configMapRef in every Deployment's envFrom).
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
  # Most of the keys below (Stripe, IM channels, less-common LLM providers)
  # are expected to be absent from any given .env — under `set -o pipefail`,
  # grep's own "no match" exit code would otherwise propagate through the
  # pipeline and (with `set -e`) kill the whole script, so it's explicitly
  # swallowed here; an absent key legitimately means "", not an error.
  local key="$1"
  { grep -E "^${key}=" "$ENV_FILE" || true; } | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}

kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

# Freshly generated for staging — do not reuse prod's real values.
BETTER_AUTH_SECRET="$(gen_secret)"
DEER_FLOW_INTERNAL_AUTH_TOKEN="$(gen_secret)"
NOVA_OPS_TOKEN="$(gen_secret)"
SEARXNG_SECRET_KEY="$(gen_secret)"
# AUTH_JWT_SECRET: deliberately generated here rather than left to
# app/gateway/auth/config.py's own auto-generate-and-persist-to-PVC
# fallback. That fallback has a real check-then-act race across replicas —
# two gateway pods starting simultaneously against a fresh PVC (first
# `helm install`, or a Phase 7 DR restore into a scratch namespace) can
# each observe "no .jwt_secret file yet", generate a DIFFERENT secret, and
# both persist to the same shared file — whichever write lands last wins
# the file, but the losing replica already cached the other value in
# memory, so it validates JWTs against a secret nothing else agrees on
# from that point forward. Supplying it as a K8s Secret sidesteps the race
# entirely: every replica sees the same value before any of them start.
AUTH_JWT_SECRET="$(gen_secret)"

# Real third-party secrets — read straight from .env when present, empty
# otherwise (kubectl accepts an empty --from-literal value; every consumer
# in this codebase already treats an empty/missing key as "feature
# disabled", not an error — see billing.py's is_billing_enabled(),
# byok.py's is_byok_enabled(), etc.).
#
# LLM / search / scraping providers
FIREWORKS_API_KEY="$(read_env_key FIREWORKS_API_KEY)"
MINIMAX_API_KEY="$(read_env_key MINIMAX_API_KEY)"
TAVILY_API_KEY="$(read_env_key TAVILY_API_KEY)"
JINA_API_KEY="$(read_env_key JINA_API_KEY)"
SERPER_API_KEY="$(read_env_key SERPER_API_KEY)"
INFOQUEST_API_KEY="$(read_env_key INFOQUEST_API_KEY)"
OPENAI_API_KEY="$(read_env_key OPENAI_API_KEY)"
GEMINI_API_KEY="$(read_env_key GEMINI_API_KEY)"
ANTHROPIC_API_KEY="$(read_env_key ANTHROPIC_API_KEY)"
DEEPSEEK_API_KEY="$(read_env_key DEEPSEEK_API_KEY)"
NOVITA_API_KEY="$(read_env_key NOVITA_API_KEY)"
STEPFUN_API_KEY="$(read_env_key STEPFUN_API_KEY)"
VLLM_API_KEY="$(read_env_key VLLM_API_KEY)"
VOLCENGINE_API_KEY="$(read_env_key VOLCENGINE_API_KEY)"
MOONSHOT_API_KEY="$(read_env_key MOONSHOT_API_KEY)"
MIMO_API_KEY="$(read_env_key MIMO_API_KEY)"
FIRECRAWL_API_KEY="$(read_env_key FIRECRAWL_API_KEY)"
BRAVE_SEARCH_API_KEY="$(read_env_key BRAVE_SEARCH_API_KEY)"
EXA_API_KEY="$(read_env_key EXA_API_KEY)"
OPENROUTER_API_KEY="$(read_env_key OPENROUTER_API_KEY)"
BROWSERLESS_TOKEN="$(read_env_key BROWSERLESS_TOKEN)"

# Tracing (LangSmith / Langfuse)
LANGSMITH_API_KEY="$(read_env_key LANGSMITH_API_KEY)"
LANGFUSE_PUBLIC_KEY="$(read_env_key LANGFUSE_PUBLIC_KEY)"
LANGFUSE_SECRET_KEY="$(read_env_key LANGFUSE_SECRET_KEY)"

# CLI/ACP subscription auth + GitHub
CLAUDE_CODE_OAUTH_TOKEN="$(read_env_key CLAUDE_CODE_OAUTH_TOKEN)"
ANTHROPIC_AUTH_TOKEN="$(read_env_key ANTHROPIC_AUTH_TOKEN)"
GITHUB_TOKEN="$(read_env_key GITHUB_TOKEN)"

# Billing (Stripe) — gated cleanly off when STRIPE_SECRET_KEY is empty
STRIPE_SECRET_KEY="$(read_env_key STRIPE_SECRET_KEY)"
STRIPE_WEBHOOK_SECRET="$(read_env_key STRIPE_WEBHOOK_SECRET)"
STRIPE_PRICE_ID_PLUS="$(read_env_key STRIPE_PRICE_ID_PLUS)"

# BYOK (bring-your-own-key) at-rest encryption — gated cleanly off when empty
NOVA_BYOK_SECRET="$(read_env_key NOVA_BYOK_SECRET)"

# Postgres (only consumed when config.yaml has database.backend: postgres)
DATABASE_URL="$(read_env_key DATABASE_URL)"

# IM channel platform credentials
FEISHU_APP_ID="$(read_env_key FEISHU_APP_ID)"
FEISHU_APP_SECRET="$(read_env_key FEISHU_APP_SECRET)"
SLACK_BOT_TOKEN="$(read_env_key SLACK_BOT_TOKEN)"
SLACK_APP_TOKEN="$(read_env_key SLACK_APP_TOKEN)"
TELEGRAM_BOT_TOKEN="$(read_env_key TELEGRAM_BOT_TOKEN)"
DISCORD_BOT_TOKEN="$(read_env_key DISCORD_BOT_TOKEN)"
WECHAT_BOT_TOKEN="$(read_env_key WECHAT_BOT_TOKEN)"
WECOM_BOT_ID="$(read_env_key WECOM_BOT_ID)"
WECOM_BOT_SECRET="$(read_env_key WECOM_BOT_SECRET)"
DINGTALK_CLIENT_ID="$(read_env_key DINGTALK_CLIENT_ID)"
DINGTALK_CLIENT_SECRET="$(read_env_key DINGTALK_CLIENT_SECRET)"

kubectl create secret generic nova-secrets \
  -n "$NAMESPACE" \
  --from-literal=BETTER_AUTH_SECRET="$BETTER_AUTH_SECRET" \
  --from-literal=DEER_FLOW_INTERNAL_AUTH_TOKEN="$DEER_FLOW_INTERNAL_AUTH_TOKEN" \
  --from-literal=NOVA_OPS_TOKEN="$NOVA_OPS_TOKEN" \
  --from-literal=SEARXNG_SECRET_KEY="$SEARXNG_SECRET_KEY" \
  --from-literal=AUTH_JWT_SECRET="$AUTH_JWT_SECRET" \
  --from-literal=FIREWORKS_API_KEY="$FIREWORKS_API_KEY" \
  --from-literal=MINIMAX_API_KEY="$MINIMAX_API_KEY" \
  --from-literal=TAVILY_API_KEY="$TAVILY_API_KEY" \
  --from-literal=JINA_API_KEY="$JINA_API_KEY" \
  --from-literal=SERPER_API_KEY="$SERPER_API_KEY" \
  --from-literal=INFOQUEST_API_KEY="$INFOQUEST_API_KEY" \
  --from-literal=OPENAI_API_KEY="$OPENAI_API_KEY" \
  --from-literal=GEMINI_API_KEY="$GEMINI_API_KEY" \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --from-literal=DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
  --from-literal=NOVITA_API_KEY="$NOVITA_API_KEY" \
  --from-literal=STEPFUN_API_KEY="$STEPFUN_API_KEY" \
  --from-literal=VLLM_API_KEY="$VLLM_API_KEY" \
  --from-literal=VOLCENGINE_API_KEY="$VOLCENGINE_API_KEY" \
  --from-literal=MOONSHOT_API_KEY="$MOONSHOT_API_KEY" \
  --from-literal=MIMO_API_KEY="$MIMO_API_KEY" \
  --from-literal=FIRECRAWL_API_KEY="$FIRECRAWL_API_KEY" \
  --from-literal=BRAVE_SEARCH_API_KEY="$BRAVE_SEARCH_API_KEY" \
  --from-literal=EXA_API_KEY="$EXA_API_KEY" \
  --from-literal=OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  --from-literal=BROWSERLESS_TOKEN="$BROWSERLESS_TOKEN" \
  --from-literal=LANGSMITH_API_KEY="$LANGSMITH_API_KEY" \
  --from-literal=LANGFUSE_PUBLIC_KEY="$LANGFUSE_PUBLIC_KEY" \
  --from-literal=LANGFUSE_SECRET_KEY="$LANGFUSE_SECRET_KEY" \
  --from-literal=CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
  --from-literal=ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_AUTH_TOKEN" \
  --from-literal=GITHUB_TOKEN="$GITHUB_TOKEN" \
  --from-literal=STRIPE_SECRET_KEY="$STRIPE_SECRET_KEY" \
  --from-literal=STRIPE_WEBHOOK_SECRET="$STRIPE_WEBHOOK_SECRET" \
  --from-literal=STRIPE_PRICE_ID_PLUS="$STRIPE_PRICE_ID_PLUS" \
  --from-literal=NOVA_BYOK_SECRET="$NOVA_BYOK_SECRET" \
  --from-literal=DATABASE_URL="$DATABASE_URL" \
  --from-literal=FEISHU_APP_ID="$FEISHU_APP_ID" \
  --from-literal=FEISHU_APP_SECRET="$FEISHU_APP_SECRET" \
  --from-literal=SLACK_BOT_TOKEN="$SLACK_BOT_TOKEN" \
  --from-literal=SLACK_APP_TOKEN="$SLACK_APP_TOKEN" \
  --from-literal=TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
  --from-literal=DISCORD_BOT_TOKEN="$DISCORD_BOT_TOKEN" \
  --from-literal=WECHAT_BOT_TOKEN="$WECHAT_BOT_TOKEN" \
  --from-literal=WECOM_BOT_ID="$WECOM_BOT_ID" \
  --from-literal=WECOM_BOT_SECRET="$WECOM_BOT_SECRET" \
  --from-literal=DINGTALK_CLIENT_ID="$DINGTALK_CLIENT_ID" \
  --from-literal=DINGTALK_CLIENT_SECRET="$DINGTALK_CLIENT_SECRET" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "nova-secrets applied in namespace $NAMESPACE."
