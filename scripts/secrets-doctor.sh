#!/usr/bin/env bash
# Report which credentials are set, which are missing, and which combinations
# are dangerous — WITHOUT ever printing a value.
#
# Safe to run anywhere, safe to paste the output into an issue or a chat.
# Companion: docs/CREDENTIALS.md (what each one is) and scripts/secrets-export.sh
# (make a real backup, which this script deliberately cannot do).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

green() { printf '\033[32m%s\033[0m' "$1"; }
red()   { printf '\033[31m%s\033[0m' "$1"; }
dim()   { printf '\033[2m%s\033[0m' "$1"; }
bold()  { printf '\033[1m%s\033[0m\n' "$1"; }

set_count=0
missing_count=0

# Is NAME assigned a non-empty value in .env? Never echoes the value.
is_set() {
  local name="$1"
  [ -f "$ENV_FILE" ] || return 1
  local line
  line="$(grep -E "^[[:space:]]*${name}=" "$ENV_FILE" 2>/dev/null | tail -1)" || return 1
  [ -n "$line" ] || return 1
  local value="${line#*=}"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  [ -n "$value" ]
}

# Read a NON-SECRET flag (e.g. DEER_FLOW_AUTH_DISABLED) for the safety checks.
flag_value() {
  local name="$1"
  [ -f "$ENV_FILE" ] || return 0
  local line
  line="$(grep -E "^[[:space:]]*${name}=" "$ENV_FILE" 2>/dev/null | tail -1)" || return 0
  local value="${line#*=}"
  value="${value%\"}"; value="${value#\"}"
  printf '%s' "$value"
}

row() {
  local name="$1" note="${2:-}"
  if is_set "$name"; then
    printf '  %s  %-34s' "$(green ' SET ')" "$name"
    set_count=$((set_count + 1))
  else
    printf '  %s  %-34s' "$(red 'MISS ')" "$name"
    missing_count=$((missing_count + 1))
  fi
  [ -n "$note" ] && dim "$note"
  echo
}

section() { echo; bold "$1"; }

echo
bold "Nova credential doctor"
dim "  source: $ENV_FILE"; echo
if [ ! -f "$ENV_FILE" ]; then
  echo "  $(red 'no .env found') — everything below will read as missing"
fi

section "Auth & session"
row BETTER_AUTH_SECRET            "required — signs sessions"
row DEER_FLOW_INTERNAL_AUTH_TOKEN "required in prod — internal calls"
row NOVA_OPS_TOKEN                "unset = /ops auth DISABLED"
row AUTH_JWT_SECRET               "optional — auto-generated if unset"
row NOVA_BYOK_SECRET              "UNRECOVERABLE if lost"
row SEARXNG_SECRET_KEY            "required for the search stack"

section "LLM providers (at least one required)"
for v in FIREWORKS_API_KEY MINIMAX_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY \
         GEMINI_API_KEY DEEPSEEK_API_KEY MOONSHOT_API_KEY OPENROUTER_API_KEY \
         NOVITA_API_KEY STEPFUN_API_KEY VOLCENGINE_API_KEY MIMO_API_KEY VLLM_API_KEY; do
  row "$v"
done

section "Search & scraping"
for v in TAVILY_API_KEY SERPER_API_KEY JINA_API_KEY INFOQUEST_API_KEY \
         BRAVE_SEARCH_API_KEY EXA_API_KEY FIRECRAWL_API_KEY BROWSERLESS_TOKEN; do
  row "$v"
done

section "Billing"
for v in STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET STRIPE_PRICE_ID_PLUS; do row "$v"; done

section "Tracing"
for v in LANGSMITH_API_KEY LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY; do row "$v"; done

section "IM channels"
for v in SLACK_BOT_TOKEN SLACK_APP_TOKEN TELEGRAM_BOT_TOKEN DISCORD_BOT_TOKEN \
         FEISHU_APP_ID FEISHU_APP_SECRET DINGTALK_CLIENT_ID DINGTALK_CLIENT_SECRET \
         WECOM_BOT_ID WECOM_BOT_SECRET WECHAT_BOT_TOKEN; do
  row "$v"
done

section "Data stores"
row DATABASE_URL "only needed with database.backend: postgres"
row REDIS_URL    "set by ConfigMap in k8s"

section "Files that hold secrets outside .env"
for f in "$REPO_ROOT/backend/.deer-flow/.jwt_secret" \
         "/etc/cloudflared/env" \
         "$REPO_ROOT/docker/monitoring/monitoring.env"; do
  if [ -e "$f" ]; then
    printf '  %s  %s\n' "$(green 'PRESENT')" "$f"
  else
    printf '  %s  %s\n' "$(dim 'absent ')" "$f"
  fi
done

# ── Safety checks ────────────────────────────────────────────────────────────
section "Safety checks"
problems=0

auth_disabled="$(flag_value DEER_FLOW_AUTH_DISABLED)"
env_name="$(flag_value DEER_FLOW_ENV)"
if [ "$auth_disabled" = "1" ]; then
  echo "  $(red 'DANGER') DEER_FLOW_AUTH_DISABLED=1 — anyone can act as the default user"
  problems=$((problems + 1))
else
  echo "  $(green '  ok  ') auth is enabled"
fi

if [ "$env_name" = "production" ] || [ "$env_name" = "prod" ]; then
  echo "  $(green '  ok  ') DEER_FLOW_ENV=$env_name (auth-disabled mode is refused here)"
  dim "         note: local test harnesses must set DEER_FLOW_ENV=test or they will 401"; echo
fi

if ! is_set NOVA_OPS_TOKEN; then
  echo "  $(red 'DANGER') NOVA_OPS_TOKEN unset — the /ops admin surface has NO auth"
  problems=$((problems + 1))
fi

if is_set NOVA_BYOK_SECRET; then
  if command -v python3 >/dev/null 2>&1; then
    # Validate WITHOUT printing: exit status only.
    if ENV_FILE="$ENV_FILE" python3 - <<'PY' >/dev/null 2>&1
import os, re, sys
src = open(os.environ["ENV_FILE"], encoding="utf-8").read()
m = re.search(r"^\s*NOVA_BYOK_SECRET=(.*)$", src, re.M)
val = (m.group(1).strip().strip('"').strip("'") if m else "")
from cryptography.fernet import Fernet
Fernet(val.encode())
PY
    then
      echo "  $(green '  ok  ') NOVA_BYOK_SECRET is a valid Fernet key"
    else
      echo "  $(red 'DANGER') NOVA_BYOK_SECRET is set but NOT a valid Fernet key — BYOK silently disabled"
      problems=$((problems + 1))
    fi
  fi
fi

if [ -f "$REPO_ROOT/config.yaml.bak" ]; then
  echo "  $(red ' warn ') config.yaml.bak exists — predates \$VAR interpolation, may hold literal keys. Audit and delete."
  problems=$((problems + 1))
fi

if git -C "$REPO_ROOT" ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "  $(red 'DANGER') .env is TRACKED BY GIT"
  problems=$((problems + 1))
else
  echo "  $(green '  ok  ') .env is not tracked by git"
fi

echo
bold "Summary"
echo "  $set_count set · $missing_count missing · $problems problem(s)"
dim "  Missing is often fine — most credentials are optional and their feature self-disables."; echo
dim "  What each one does: docs/CREDENTIALS.md    Real backup: scripts/secrets-export.sh"; echo
echo

[ "$problems" -eq 0 ]
