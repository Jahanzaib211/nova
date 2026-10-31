#!/usr/bin/env bash
#
# Run a cloudflared connector for a token-authenticated tunnel, as the PM2 user.
#
# Why this exists next to the systemd units: the systemd connectors read their
# token from a root-owned file under /etc/cloudflared/. If that file is lost —
# a wiped /etc, a re-image, a half-finished uninstall — the unit cannot start
# and the public hostname goes down with nothing but a flapping restart loop
# to show for it. This runner takes the token from a 0600 file the PM2 user
# owns, so the connector can be brought back without root.
#
# It is a *fallback*, not the primary setup: the systemd unit owns the
# lifecycle on a healthy host. Use this when /etc/cloudflared is unavailable
# and the goal is to get the hostname back up quickly.
#
# Usage: pm2-cloudflared-token.sh <name> <token-file>
#
# The token file must contain only the token, no trailing newline.

set -euo pipefail

NAME="${1:-tunnel}"
TOKEN_FILE="${2:-${TOKEN_FILE:-}}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-/usr/local/bin/cloudflared}"

if [ -z "$TOKEN_FILE" ]; then
  echo "[cloudflared] no token file given (arg 2 or \$TOKEN_FILE)" >&2
  exit 1
fi

if [ ! -x "$CLOUDFLARED_BIN" ]; then
  echo "[cloudflared] $CLOUDFLARED_BIN is not executable" >&2
  exit 1
fi
if [ ! -r "$TOKEN_FILE" ]; then
  echo "[cloudflared] token file $TOKEN_FILE is missing or unreadable —" >&2
  echo "[cloudflared] restore it (Cloudflare dashboard → Zero Trust → Networks →" >&2
  echo "[cloudflared] Tunnels → <tunnel> → Configure → Install connector) and" >&2
  echo "[cloudflared] restart this app." >&2
  exit 1
fi

# The token must be readable by this user only.
chmod 600 "$TOKEN_FILE" 2>/dev/null || true

exec "$CLOUDFLARED_BIN" --no-autoupdate tunnel run --token-file "$TOKEN_FILE"
