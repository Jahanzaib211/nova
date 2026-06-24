#!/usr/bin/env bash
# status.sh — Check DeerFlow status and list available resources.
#
# Usage:
#   bash status.sh                  # health + summary
#   bash status.sh models           # list models
#   bash status.sh skills           # list skills
#   bash status.sh agents           # list agents
#   bash status.sh threads          # list recent threads
#   bash status.sh memory           # show memory
#   bash status.sh thread <id>      # show thread history
#
# Environment variables:
#   DEERFLOW_URL           — Unified proxy base URL (default: http://localhost:2026)
#   DEERFLOW_GATEWAY_URL   — Gateway API base URL (default: $DEERFLOW_URL)
#   DEERFLOW_LANGGRAPH_URL — LangGraph API base URL (default: $DEERFLOW_URL/api/langgraph)

set -euo pipefail

DEERFLOW_URL="${DEERFLOW_URL:-http://localhost:2026}"
GATEWAY_URL="${DEERFLOW_GATEWAY_URL:-$DEERFLOW_URL}"
LANGGRAPH_URL="${DEERFLOW_LANGGRAPH_URL:-$DEERFLOW_URL/api/langgraph}"
CMD="${1:-health}"
ARG="${2:-}"

# ── Auth helper ────────────────────────────────────────────────────────────
# If DEERFLOW_EMAIL/DEERFLOW_PASSWORD are set, log in to get session + CSRF
# cookies. Falls back to unauthenticated if auth is disabled on the server.
_COOKIE_JAR=$(mktemp)
trap "rm -f '$_COOKIE_JAR'" EXIT
_CSRF_TOKEN=""

_deerflow_login() {
  local email="${DEERFLOW_EMAIL:-}"
  local password="${DEERFLOW_PASSWORD:-}"
  [ -z "$email" ] || [ -z "$password" ] && return 0

  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    -c "$_COOKIE_JAR" \
    -X POST "${GATEWAY_URL}/api/v1/auth/login/local" \
    -d "username=${email}&password=${password}" 2>/dev/null || echo "000")

  if [ "$http_code" -ge 200 ] && [ "$http_code" -lt 300 ]; then
    _CSRF_TOKEN=$(grep "csrf_token" "$_COOKIE_JAR" 2>/dev/null | awk '{print $NF}' | tail -1)
  fi
}

_curl() {
  if [ -n "$_CSRF_TOKEN" ]; then
    curl -b "$_COOKIE_JAR" -H "X-CSRF-Token: $_CSRF_TOKEN" "$@"
  else
    curl "$@"
  fi
}

_deerflow_login

case "$CMD" in
  health)
    echo "Checking DeerFlow at ${GATEWAY_URL}..."
    HTTP_CODE=$(_curl -s -o /dev/null -w "%{http_code}" "${GATEWAY_URL}/health" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "000" ]; then
      echo "UNREACHABLE — DeerFlow is not running at ${GATEWAY_URL}"
      exit 1
    elif [ "$HTTP_CODE" -ge 400 ]; then
      echo "ERROR — Health check returned HTTP ${HTTP_CODE}"
      exit 1
    else
      echo "OK — DeerFlow is running (HTTP ${HTTP_CODE})"
    fi
    ;;
  models)
    _curl -s "${GATEWAY_URL}/api/models" | python3 -m json.tool
    ;;
  skills)
    _curl -s "${GATEWAY_URL}/api/skills" | python3 -m json.tool
    ;;
  agents)
    _curl -s "${GATEWAY_URL}/api/agents" | python3 -m json.tool
    ;;
  threads)
    _curl -s -X POST "${LANGGRAPH_URL}/threads/search" \
      -H "Content-Type: application/json" \
      -d '{"limit": 20, "sort_by": "updated_at", "sort_order": "desc", "select": ["thread_id", "updated_at", "values"]}' \
      | python3 -c "
import json, sys
threads = json.load(sys.stdin)
if not threads:
    print('No threads found.')
    sys.exit(0)
for t in threads:
    tid = t.get('thread_id', '?')
    updated = t.get('updated_at', '?')
    title = (t.get('values') or {}).get('title', '(untitled)')
    print(f'{tid}  {updated}  {title}')
"
    ;;
  memory)
    _curl -s "${GATEWAY_URL}/api/memory" | python3 -m json.tool
    ;;
  thread)
    if [ -z "$ARG" ]; then
      echo "Usage: status.sh thread <thread_id>" >&2
      exit 1
    fi
    _curl -s "${LANGGRAPH_URL}/threads/${ARG}/history" | python3 -c "
import json, sys
data = json.load(sys.stdin)
if isinstance(data, list):
    for state in data[:5]:
        values = state.get('values', {})
        msgs = values.get('messages', [])
        for m in msgs[-5:]:
            role = m.get('type', '?')
            content = m.get('content', '')
            if isinstance(content, list):
                content = ' '.join(p.get('text','') for p in content if isinstance(p, dict))
            preview = content[:200] if content else '(empty)'
            print(f'[{role}] {preview}')
        print('---')
else:
    print(json.dumps(data, indent=2))
"
    ;;
  *)
    echo "Unknown command: ${CMD}" >&2
    echo "Usage: status.sh [health|models|skills|agents|threads|memory|thread <id>]" >&2
    exit 1
    ;;
esac
