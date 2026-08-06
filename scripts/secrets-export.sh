#!/usr/bin/env bash
# Make a real, restorable backup of this machine's secrets.
#
# This is the only script that touches actual values, it runs entirely on your
# machine, and it writes a 0600 file that is gitignored. Nothing is printed to
# the terminal and nothing is sent anywhere.
#
# Restore on a new machine:  cp secrets-backup-<date>.env .env
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$REPO_ROOT/secrets-backup-$(date +%Y%m%d-%H%M%S).env}"

case "$OUT" in
  *.env) ;;
  *) echo "refusing to write '$OUT' — the filename must end in .env so .gitignore covers it" >&2; exit 1 ;;
esac

umask 077   # anything created below is 0600 from birth, not chmod'd after

{
  echo "# Nova secret backup — $(date -Iseconds) — host $(hostname)"
  echo "# Restore with:  cp \"$(basename "$OUT")\" .env"
  echo "# TREAT THIS FILE LIKE A PASSWORD. Never commit it, never paste it."
  echo

  if [ -f "$REPO_ROOT/.env" ]; then
    echo "# ── root .env ────────────────────────────────────────────────"
    cat "$REPO_ROOT/.env"
    echo
  fi

  if [ -f "$REPO_ROOT/frontend/.env" ]; then
    echo "# ── frontend/.env ───────────────────────────────────────────"
    sed 's/^/# /' "$REPO_ROOT/frontend/.env"
    echo
  fi

  # The JWT secret is a bare file, auto-generated once, and is the single copy
  # of the thing that keeps every user logged in.
  if [ -f "$REPO_ROOT/backend/.deer-flow/.jwt_secret" ]; then
    echo "# ── backend/.deer-flow/.jwt_secret (restore as that file, mode 0600) ──"
    echo "# AUTH_JWT_SECRET_FILE_CONTENT=$(cat "$REPO_ROOT/backend/.deer-flow/.jwt_secret")"
    echo
  fi

  if [ -r /etc/cloudflared/env ]; then
    echo "# ── /etc/cloudflared/env (restore as that path, mode 0600) ──"
    sed 's/^/# /' /etc/cloudflared/env
    echo
  else
    echo "# /etc/cloudflared/env not readable as this user — back it up with sudo:"
    echo "#   sudo cat /etc/cloudflared/env"
    echo
  fi

  if [ -f "$REPO_ROOT/docker/monitoring/monitoring.env" ]; then
    echo "# ── docker/monitoring/monitoring.env ────────────────────────"
    sed 's/^/# /' "$REPO_ROOT/docker/monitoring/monitoring.env"
    echo
  fi
} > "$OUT"

chmod 600 "$OUT"

# Fail loudly rather than leave a secret file git could pick up.
if ! git -C "$REPO_ROOT" check-ignore -q "$OUT" 2>/dev/null; then
  echo "WARNING: $OUT is NOT gitignored. Move it outside the repo now." >&2
fi

echo "Wrote $(wc -l < "$OUT") lines to:"
echo "  $OUT"
echo
echo "  mode:      $(stat -c '%a' "$OUT")"
echo "  gitignored: $(git -C "$REPO_ROOT" check-ignore -q "$OUT" 2>/dev/null && echo yes || echo 'NO — MOVE IT')"
echo
echo "Store it in a password manager or an encrypted volume. Do not leave it here."
