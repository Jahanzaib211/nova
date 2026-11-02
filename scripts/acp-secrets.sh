#!/bin/sh
# Materialise what the ACP agents overlays mount (docker/docker-compose.acp.yaml
# and docker/docker-compose.cli-auth.yaml). Idempotent; safe to run on every
# stack start, and safe to re-run by hand at any time — both outputs land in
# directories that are mounted as directories, so a re-run is visible inside
# the running containers immediately, with no recreation.
#
# 1. Claude Code's account/onboarding state, ~/.claude.json.
#    It used to be bind-mounted as a single file. The CLI rewrites it by
#    write-then-rename, which gives the host path a new inode, and a single-file
#    bind keeps serving the inode that existed at container creation — so the
#    container read an ever older copy until it was recreated (the drift gate's
#    file_mounts check reported it yellow). It is copied here into
#    ~/.nova/claude-state/ (0700 dir, 0600 file); the cli-auth overlay mounts
#    that directory read-only and /root/.claude.json in the container is a
#    symlink into it. The credential itself (~/.claude/.credentials.json) is
#    not copied: it stays in the ~/.claude directory mount, which is live.
#
# 2. OpenClaw's gateway token. It lives in ~/.openclaw/openclaw.json next to a
#    lot of other state. The container must not see that file; it gets exactly
#    the token, in a 0600 file under ~/.nova/secrets, mounted read-only at
#    /run/nova/openclaw_token.
set -eu

# ── 1. Claude Code state ────────────────────────────────────────────────────
CJ_SRC="${CLAUDE_JSON:-$HOME/.claude.json}"
CJ_DIR="${NOVA_CLAUDE_STATE_DIR:-$HOME/.nova/claude-state}"
mkdir -p "$CJ_DIR"
chmod 700 "$CJ_DIR"
if [ -r "$CJ_SRC" ]; then
  # Same-directory temp file + rename: a reader in the container sees either
  # the old copy or the new one, never a half-written file.
  (
    umask 077
    cp "$CJ_SRC" "$CJ_DIR/.claude.json.tmp"
  )
  chmod 600 "$CJ_DIR/.claude.json.tmp"
  mv -f "$CJ_DIR/.claude.json.tmp" "$CJ_DIR/.claude.json"
  echo "acp-secrets: staged $CJ_DIR/.claude.json" >&2
else
  echo "acp-secrets: $CJ_SRC not readable — Claude Code in the container will report 'configuration file not found'" >&2
fi

# ── 2. OpenClaw token ───────────────────────────────────────────────────────
SRC="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"
DIR="${NOVA_SECRETS_DIR:-$HOME/.nova/secrets}"
OUT="$DIR/openclaw_token"

mkdir -p "$DIR"
chmod 700 "$DIR"
if [ ! -r "$SRC" ]; then
  echo "acp-secrets: $SRC not readable — openclaw agent will not authenticate" >&2
  exit 0
fi
token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["gateway"]["auth"]["token"])' "$SRC" 2>/dev/null || true)"
if [ -z "$token" ]; then
  echo "acp-secrets: no gateway.auth.token in $SRC (auth mode is not token?)" >&2
  exit 0
fi
umask 077
printf '%s' "$token" > "$OUT.tmp"
mv -f "$OUT.tmp" "$OUT"
chmod 600 "$OUT"
echo "acp-secrets: wrote $OUT" >&2
