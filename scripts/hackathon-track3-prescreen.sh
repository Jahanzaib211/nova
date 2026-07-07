#!/usr/bin/env bash
# Track 3 pre-screen self-audit.
#
# The hackathon's automated pre-screen inspects the GitHub repo, the slide deck
# (PDF), and the live demo/hosted URL for AMD-compute usage and originality. This
# script checks the pieces we control, so we catch a miss before submitting.
#
# Repo checks are fatal. Live-demo checks run only when NOVA_LIVE_URL is set.
#
#   NOVA_LIVE_URL=https://nova.example.com ./scripts/hackathon-track3-prescreen.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
ok()   { printf "  \033[32mPASS\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31mFAIL\033[0m %s\n" "$1"; fail=1; }
note() { printf "  \033[33m····\033[0m %s\n" "$1"; }

echo "== Repo: AMD-compute evidence =="
[ -f "$ROOT/docs/AMD_INTEGRATION.md" ] && ok "docs/AMD_INTEGRATION.md present" || bad "docs/AMD_INTEGRATION.md missing"
[ -f "$ROOT/scripts/amd-serve-vllm.sh" ] && ok "AMD vLLM/ROCm serving script present" || bad "scripts/amd-serve-vllm.sh missing"
[ -f "$ROOT/hackathon/track3/deck.pdf" ] && ok "slide deck PDF present" || bad "hackathon/track3/deck.pdf missing"
grep -q "detect_amd_compute" "$ROOT/backend/app/gateway/routers/models.py" 2>/dev/null \
  && ok "amd-usage detection in gateway" || bad "detect_amd_compute not found in models router"
grep -q "amd-usage" "$ROOT/backend/app/gateway/routers/models.py" 2>/dev/null \
  && ok "/api/models/amd-usage endpoint present" || bad "amd-usage endpoint not found"
grep -qi "AMD compute" "$ROOT/config.example.yaml" 2>/dev/null \
  && ok "AMD provider examples in config.example.yaml" || bad "AMD examples missing from config.example.yaml"
grep -qi "AMD" "$ROOT/README.md" 2>/dev/null \
  && ok "README references AMD" || bad "README has no AMD section"

echo "== Live demo (optional) =="
if [ -n "${NOVA_LIVE_URL:-}" ]; then
  base="${NOVA_LIVE_URL%/}"
  code=$(curl -s -o /dev/null -w "%{http_code}" "$base/" 2>/dev/null || echo 000)
  [ "$code" = "200" ] && ok "live demo reachable ($base → 200)" || bad "live demo not 200 ($base → $code)"
  body=$(curl -s "$base/api/models/amd-usage" 2>/dev/null || echo "")
  if printf '%s' "$body" | grep -q '"amd_backed"[[:space:]]*:[[:space:]]*true'; then
    ok "live /api/models/amd-usage reports amd_backed=true"
  else
    bad "live amd-usage did not report amd_backed=true (configure an AMD model)"
  fi
else
  note "NOVA_LIVE_URL not set — skipping live-demo checks"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "Track 3 pre-screen: ALL REPO CHECKS PASSED"
else
  echo "Track 3 pre-screen: FAILURES ABOVE — fix before submitting" >&2
fi
exit "$fail"
