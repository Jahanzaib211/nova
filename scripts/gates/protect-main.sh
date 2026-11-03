#!/usr/bin/env bash
# Protect main: every change arrives as a pull request that a human merges.
#
# Why this shape
# --------------
# - A pull request is required, with 0 approvals: GitHub does not let a PR's
#   author approve it, so on a single-maintainer repo a required approval would
#   block every PR. The merge click *is* the human approval -- nothing reaches
#   main without the owner choosing to merge it.
# - enforce_admins: the protection binds the owner too. Agents act with the
#   owner's token, so without it "admin" means "anyone holding the token".
# - Required checks are the ones that run on *every* PR. A required check whose
#   workflow is path-filtered or schedule-only never reports on most PRs, and a
#   check that never reports blocks the PR forever. The preflight below refuses
#   to require a context that main's HEAD has not reported.
# - Force pushes and deletion are blocked; review conversations must be
#   resolved before merging.
#
# Idempotent: the PUT replaces the whole protection with exactly this payload.
#
# Usage:
#   scripts/gates/protect-main.sh --dry-run   # print payload + preflight, change nothing
#   scripts/gates/protect-main.sh             # apply (prints before/after)
#   scripts/gates/protect-main.sh --force     # apply even if a context has not reported yet
set -euo pipefail

REPO="${NOVA_REPO:-Jahanzaib211/nova}"
BRANCH="${NOVA_PROTECTED_BRANCH:-main}"

# Job names (check-run contexts). Keep in step with the workflows:
#   Upstream isolation   upstream-isolation.yml   (the history gate)
#   local-ci-gate        local-ci.yml             (aggregates backend lint/format/tests/
#                                                  blocking-io/coverage/cross-ref, frontend
#                                                  lint/tests/build, dependency audit)
#   backend-unit-tests   backend-unit-tests.yml
#   frontend-unit-tests  frontend-unit-tests.yml
#   lint-backend         lint-check.yml
#   lint-frontend        lint-check.yml
# Deliberately absent: path-filtered workflows (blocking-io, docs, e2e, frontend
# build/visual, lighthouse, replay) and schedule-only jobs.
CHECKS=("Upstream isolation" "local-ci-gate" "backend-unit-tests" "frontend-unit-tests" "lint-backend" "lint-frontend")

mode="apply"
case "${1:-}" in
  --dry-run) mode="dry-run" ;;
  --force) mode="force" ;;
  "") ;;
  *) echo "usage: $0 [--dry-run|--force]" >&2; exit 2 ;;
esac

payload="$(python3 -c '
import json, sys
print(json.dumps({
    "required_status_checks": {"strict": True, "checks": [{"context": c} for c in sys.argv[1:]]},
    "enforce_admins": True,
    "required_pull_request_reviews": {"required_approving_review_count": 0, "dismiss_stale_reviews": True, "require_code_owner_reviews": False},
    "restrictions": None,
    "required_linear_history": False,
    "allow_force_pushes": False,
    "allow_deletions": False,
    "required_conversation_resolution": True,
    "block_creations": False,
    "lock_branch": False,
    "allow_fork_syncing": False,
}, indent=2))
' "${CHECKS[@]}")"

echo "== payload for ${REPO}@${BRANCH}"
echo "$payload"

echo "== preflight: contexts reported on ${BRANCH} HEAD"
reported="$(gh api "repos/${REPO}/commits/${BRANCH}/check-runs" --paginate -q '.check_runs[].name' 2>/dev/null | sort -u || true)"
missing=()
for c in "${CHECKS[@]}"; do
  if grep -Fxq -- "$c" <<<"$reported"; then
    echo "  ok       $c"
  else
    echo "  MISSING  $c"
    missing+=("$c")
  fi
done

if [ "$mode" = "dry-run" ]; then
  echo "== dry run: nothing changed"
  exit 0
fi

if [ "${#missing[@]}" -gt 0 ] && [ "$mode" != "force" ]; then
  echo "refusing: ${#missing[@]} required context(s) have never reported on ${BRANCH}; merge their workflows first, or pass --force" >&2
  exit 1
fi

echo "== before"
gh api "repos/${REPO}/branches/${BRANCH}/protection" 2>/dev/null || echo "  (unprotected)"

gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" -H "Accept: application/vnd.github+json" --input - <<<"$payload" >/dev/null

echo "== after"
gh api "repos/${REPO}/branches/${BRANCH}/protection"
echo "== ${BRANCH} is protected: changes now land only through a pull request you merge"
