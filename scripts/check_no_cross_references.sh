#!/usr/bin/env bash
# One-liner for AI self-test: enforce cross-project isolation (see AGENTS.md).
#
# Usage:
#   bash scripts/check_no_cross_references.sh
#
# Exit codes:
#   0 — clean
#   1 — violations found

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

exec python3 "$REPO_ROOT/backend/tests/test_no_cross_references.py" "$@"
