#!/usr/bin/env bash
# Compatibility wrapper: the launcher was renamed to scripts/pm2-nova.sh. A PM2
# registration saved before the rename (`pm2 save` dump) still points here.
# Remove after one release, once `pm2 describe nova` shows pm2-nova.sh.
exec "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/pm2-nova.sh" "$@"
