#!/usr/bin/env bash
# migrate-deerflow-to-nova.sh — operator-run cutover of a live host from the
# legacy deer-flow names to the nova names.
#
#   compose project   deer-flow-dev                 -> nova
#   containers        deer-flow-<svc>               -> nova-<svc>
#   network           deer-flow-dev_deer-flow-dev   -> nova   (same 192.168.200.0/24, same pinned IPs)
#   volumes           deer-flow-dev_<key>           -> nova_<key>   (COPIED; the old ones are never deleted)
#   images            deer-flow-dev-<svc>:latest    -> nova-<svc>:latest (tagged; the launcher runs --no-build)
#   data dir          backend/.deer-flow            -> backend/.nova   (+ symlink .deer-flow -> .nova)
#   PM2               nova app re-registered on scripts/pm2-nova.sh
#
# DRY-RUN BY DEFAULT: without --apply nothing is changed — every step reports
# what it would do (and the exact command) after read-only checks. Run it from
# the MERGED checkout (the one PM2 runs), when no agent run is in flight.
#
# Idempotent and resumable: every step checks the real state first (volume
# exists and was verified, symlink in place, ...) and skips what is done; the
# preflight row counts are kept in the state dir so a resumed run still
# compares against the pre-cutover numbers.
#
# Usage:
#   scripts/migrate-deerflow-to-nova.sh                        # dry-run plan
#   scripts/migrate-deerflow-to-nova.sh --apply --backup-dir ~/backups/nova
#   scripts/migrate-deerflow-to-nova.sh --rollback-help         # print rollback commands
#
# Options:
#   --apply               execute (asks for confirmation unless --yes)
#   --yes                 no confirmation prompt
#   --backup-dir DIR      where the pre-cutover Postgres dump lives / is written.
#                         Required for --apply. A dump (*.dump|*.sql|*.sql.gz)
#                         younger than --backup-max-age-h counts; otherwise one
#                         is taken with pg_dump -Fc before anything is stopped.
#   --backup-max-age-h N  default 24
#   --skip-caches         do not copy gateway-venv / gateway-uv-cache /
#                         gateway-npm-cache (they are rebuilt on boot, slowly)
#   --health-timeout S    postflight wait for the gateway, default 600
#   --root DIR            repo checkout to act on (default: this script's repo)
#   --rollback-help       print the rollback procedure and exit
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OLD_PROJECT="deer-flow-dev"
NEW_PROJECT="nova"
OLD_PREFIX="deer-flow"
NEW_PREFIX="nova"
OLD_NETWORKS=("deer-flow-dev_deer-flow-dev" "deer-flow-dev_default")
NEW_NETWORK="nova"
SUBNET="192.168.200.0/24"
REQUIRED_VOLUMES=(nova-postgres-data)
OPTIONAL_VOLUMES=(ollama-models)
CACHE_VOLUMES=(gateway-venv gateway-uv-cache gateway-npm-cache)
IMAGES=(gateway frontend provisioner)
HELPER_IMAGE="${NOVA_MIGRATE_HELPER_IMAGE:-alpine:3}"
STATE_DIR="${NOVA_MIGRATION_STATE_DIR:-$HOME/.nova/migration-deerflow-to-nova}"
PM2_APP="nova"
PM2_SIDECARS=(nova-healthcheck nova-gates)   # would "heal" the stack mid-cutover

APPLY=0 YES=0 SKIP_CACHES=0 BACKUP_DIR="" BACKUP_MAX_AGE_H=24 HEALTH_TIMEOUT=600

rollback_help() {
  cat <<EOF
── Rollback (exact commands; run from $REPO_ROOT) ─────────────────────────────
The legacy volumes, images and data were never deleted, so rollback is a
switch back. Anything written AFTER the cutover (new rows in nova_nova-postgres-data,
files under backend/.nova) is NOT carried back — dump it first if it matters:
  docker exec ${NEW_PREFIX}-postgres pg_dump -U \${NOVA_PG_USER:-nova} -Fc \${NOVA_PG_DB:-nova} > ~/nova-post-cutover.dump

  pm2 stop ${PM2_SIDECARS[*]} ${PM2_APP}
  docker compose -p ${NEW_PROJECT} down                      # NO -v: keeps the nova_* volumes
  # data dir back (only if .deer-flow is the symlink this script created):
  [ -L backend/.deer-flow ] && rm backend/.deer-flow && mv backend/.nova backend/.deer-flow
  # the legacy network (down removed it; the legacy-names overlay attaches it as external):
  docker network inspect deer-flow-dev_deer-flow-dev >/dev/null 2>&1 || \\
    docker network create --driver bridge --subnet ${SUBNET} --gateway 192.168.200.1 \\
      --label com.docker.compose.project=${OLD_PROJECT} --label com.docker.compose.network=${OLD_PROJECT} \\
      deer-flow-dev_deer-flow-dev
  # Option A — keep the new code, run it under the legacy names:
  pm2 delete ${PM2_APP}
  NOVA_COMPOSE_PROJECT=${OLD_PROJECT} pm2 start ecosystem.config.js --only ${PM2_APP} --update-env
  pm2 save
  pm2 restart ${PM2_SIDECARS[*]}
  #   (scripts/pm2-nova.sh then runs -p ${OLD_PROJECT} with NOVA_CONTAINER_PREFIX=${OLD_PREFIX}
  #    and docker/docker-compose.legacy-names.yaml, on the untouched ${OLD_PROJECT}_* volumes)
  # Option B — back to the pre-rename code entirely:
  #   git checkout <commit before the rename merge> -- . && pm2 delete ${PM2_APP} && \\
  #   pm2 start ecosystem.config.js --only ${PM2_APP} && pm2 save && pm2 restart ${PM2_SIDECARS[*]}
  # Then, once the legacy stack is healthy, optionally free the space of the copies:
  #   docker volume rm nova_nova-postgres-data nova_ollama-models nova_gateway-venv nova_gateway-uv-cache nova_gateway-npm-cache
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --yes|-y) YES=1 ;;
    --skip-caches) SKIP_CACHES=1 ;;
    --backup-dir) BACKUP_DIR="${2:?--backup-dir needs a path}"; shift ;;
    --backup-dir=*) BACKUP_DIR="${1#*=}" ;;
    --backup-max-age-h) BACKUP_MAX_AGE_H="${2:?}"; shift ;;
    --health-timeout) HEALTH_TIMEOUT="${2:?}"; shift ;;
    --root) REPO_ROOT="$(cd "${2:?--root needs a path}" && pwd)"; shift ;;
    --rollback-help) rollback_help; exit 0 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$REPO_ROOT"

# ── output helpers ──────────────────────────────────────────────────────────
MODE="DRY-RUN"; [ "$APPLY" = 1 ] && MODE="APPLY"
BLOCKERS=0
say()   { printf '%s\n' "$*"; }
step()  { printf '\n== %s\n' "$*"; }
st()    { printf '   [%-7s] %s\n' "$1" "$2"; }           # status line
cmd()   { printf '             $ %s\n' "$*"; }
STRICT=0   # set after the preflight: in apply mode a later blocker stops the run at once
block() {
  st BLOCKED "$*"; BLOCKERS=$((BLOCKERS + 1))
  if [ "$APPLY" = 1 ] && [ "$STRICT" = 1 ]; then
    say ""; say "Stopped at a blocker. Steps already done stay done (they are idempotent); fix it and re-run, or: $0 --rollback-help"
    exit 1
  fi
}
# run: print the command; execute it only in apply mode.
run() {
  cmd "$*"
  if [ "$APPLY" = 1 ]; then "$@"; fi
}

env_value() {  # value of KEY in .env (non-secret keys only), else $2
  local v=""
  [ -f "$REPO_ROOT/.env" ] && v="$(sed -n "s/^[[:space:]]*$1=//p" "$REPO_ROOT/.env" | tail -1 | tr -d "\"'")"
  printf '%s' "${v:-$2}"
}
PG_USER="${NOVA_PG_USER:-$(env_value NOVA_PG_USER nova)}"
PG_DB="${NOVA_PG_DB:-$(env_value NOVA_PG_DB nova)}"

exists_container() { docker inspect "$1" >/dev/null 2>&1; }
running_container() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }
exists_volume() { docker volume inspect "$1" >/dev/null 2>&1; }
exists_network() { docker network inspect "$1" >/dev/null 2>&1; }
psql_in() {  # psql_in <container> <sql>  (read-only use only)
  docker exec "$1" psql -U "$PG_USER" -d "$PG_DB" -tA -v ON_ERROR_STOP=1 -c "$2"
}
volume_fingerprint() {  # "<files> <bytes>" of a volume, via the helper image
  docker run --rm -v "$1":/v:ro "$HELPER_IMAGE" sh -c 'echo "$(find /v | wc -l) $(du -sk /v | cut -f1)"'
}

say "Nova rename cutover — $MODE   (repo: $REPO_ROOT, state: $STATE_DIR)"
[ "$APPLY" = 1 ] || say "Nothing will be changed. Re-run with --apply --backup-dir DIR to execute."

# ── 0. preflight ────────────────────────────────────────────────────────────
step "0. Preflight"
if docker info >/dev/null 2>&1; then st OK "docker daemon reachable"; else block "docker daemon not reachable"; fi
if command -v pm2 >/dev/null 2>&1; then st OK "pm2 present"; else block "pm2 not on PATH"; fi
if [ -x "$REPO_ROOT/scripts/pm2-nova.sh" ] && grep -q 'name: ${NOVA_COMPOSE_PROJECT:-nova}' "$REPO_ROOT/docker/docker-compose-dev.yaml"; then
  st OK "checkout carries the rename (scripts/pm2-nova.sh, compose name: nova)"
else
  block "this checkout predates the rename — merge it first, then run from here"
fi
if docker image inspect "$HELPER_IMAGE" >/dev/null 2>&1; then
  st OK "helper image $HELPER_IMAGE present"
else
  st TODO "helper image $HELPER_IMAGE missing — will be pulled"; cmd "docker pull $HELPER_IMAGE"
fi

OLD_PG="${OLD_PREFIX}-postgres" NEW_PG="${NEW_PREFIX}-postgres"
[ "$APPLY" = 1 ] && mkdir -p "$STATE_DIR"
COUNTS_FILE="$STATE_DIR/preflight-counts.tsv"
if running_container "$OLD_PG"; then
  pending="$(psql_in "$OLD_PG" "select count(*) from runs where status in ('pending','running')" 2>/dev/null || echo "?")"
  if [ "$pending" = "0" ]; then
    st OK "no runs pending/running in $OLD_PG"
  elif [ "$pending" = "?" ]; then
    block "could not query runs in $OLD_PG (user=$PG_USER db=$PG_DB)"
  else
    block "$pending run(s) pending/running — wait for them (saving backend code also cancels them)"
  fi
  # Table list with planner estimates (cheap). Exact counts are taken in apply
  # mode only, right before the stack stops.
  tables="$(psql_in "$OLD_PG" "select count(*) || ' tables, ~' || coalesce(sum(greatest(c.reltuples,0))::bigint,0) || ' rows (planner estimate)' from pg_stat_user_tables s join pg_class c on c.oid = s.relid" 2>/dev/null || echo "unknown")"
  st INFO "$OLD_PG: $tables"
  if [ "$APPLY" = 1 ] && [ ! -s "$COUNTS_FILE" ] && [ "$BLOCKERS" = 0 ]; then
    : > "$COUNTS_FILE.tmp"
    for t in $(psql_in "$OLD_PG" "select quote_ident(schemaname)||'.'||quote_ident(relname) from pg_stat_user_tables order by 1"); do
      printf '%s\t%s\n' "$t" "$(psql_in "$OLD_PG" "select count(*) from $t")" >> "$COUNTS_FILE.tmp"
    done
    mv "$COUNTS_FILE.tmp" "$COUNTS_FILE"
    st DONE "exact row counts of $(wc -l < "$COUNTS_FILE") tables saved to $COUNTS_FILE"
  fi
elif [ -s "$COUNTS_FILE" ]; then
  st OK "$OLD_PG not running; resuming with saved preflight counts ($COUNTS_FILE)"
elif running_container "$NEW_PG"; then
  st OK "$NEW_PG already running — cutover looks done; postflight checks only"
else
  block "$OLD_PG is not running and no saved preflight counts exist — start the legacy stack (pm2 start $PM2_APP) and re-run"
fi
[ -s "$COUNTS_FILE" ] || [ "$APPLY" = 1 ] || cmd "(apply mode records exact per-table counts to $COUNTS_FILE before stopping)"

# Backup
if [ -z "$BACKUP_DIR" ]; then
  if [ "$APPLY" = 1 ]; then block "--backup-dir is required with --apply"; else st TODO "pass --backup-dir DIR (a fresh pg_dump is required before --apply)"; fi
else
  fresh="$(find "$BACKUP_DIR" -maxdepth 1 -type f \( -name '*.dump' -o -name '*.sql' -o -name '*.sql.gz' \) -size +0 -mmin "-$((BACKUP_MAX_AGE_H * 60))" 2>/dev/null | sort | tail -1 || true)"
  if [ -n "$fresh" ]; then
    st OK "backup present: $fresh"
  elif running_container "$OLD_PG"; then
    dump="$BACKUP_DIR/nova-pre-rename-$(date +%Y%m%d-%H%M%S).dump"
    st TODO "no dump younger than ${BACKUP_MAX_AGE_H}h in $BACKUP_DIR — taking one"
    cmd "docker exec $OLD_PG pg_dump -U $PG_USER -Fc $PG_DB > $dump"
    if [ "$APPLY" = 1 ] && [ "$BLOCKERS" = 0 ]; then
      mkdir -p "$BACKUP_DIR"
      docker exec "$OLD_PG" pg_dump -U "$PG_USER" -Fc "$PG_DB" > "$dump.part" && mv "$dump.part" "$dump"
      [ -s "$dump" ] || { echo "pg_dump produced an empty file" >&2; exit 1; }
      st DONE "backup written: $dump ($(du -h "$dump" | cut -f1))"
    fi
  else
    block "no fresh dump in $BACKUP_DIR and $OLD_PG is not running to take one"
  fi
fi

if [ "$BLOCKERS" -gt 0 ]; then
  say ""; say "Preflight: $BLOCKERS blocker(s)."
  if [ "$APPLY" = 1 ]; then say "Nothing was changed."; exit 1; fi
  say "(continuing the dry-run plan below for review)"
fi

STRICT=1
if [ "$APPLY" = 1 ] && [ "$YES" = 0 ]; then
  printf '\nThis stops the public stack for the duration of the copy. Proceed? [y/N] '
  read -r answer
  case "$answer" in y|Y|yes) ;; *) say "aborted"; exit 1 ;; esac
fi

# ── 1. stop supervisors and the stack (via PM2, never docker directly) ──────
step "1. Stop PM2 apps (the watchdog first, so it cannot 'heal' the stack mid-cutover)"
pm2_status() { pm2 jlist 2>/dev/null | python3 -c "import json,sys
d={p['name']:p['pm2_env'].get('status') for p in json.load(sys.stdin)}
print(d.get(sys.argv[1],'absent'))" "$1" 2>/dev/null || echo unknown; }
for app in "${PM2_SIDECARS[@]}" "$PM2_APP"; do
  s="$(pm2_status "$app")"
  if [ "$s" = "online" ]; then st TODO "pm2 app $app is online"; run pm2 stop "$app"; else st SKIP "pm2 app $app is $s"; fi
done

step "2. Bring the legacy compose project down (no -v: volumes stay)"
if [ -n "$(docker ps -a -q --filter "label=com.docker.compose.project=$OLD_PROJECT" 2>/dev/null)" ]; then
  st TODO "containers of project $OLD_PROJECT exist"
  run docker compose -p "$OLD_PROJECT" down --timeout 60
else
  st SKIP "no containers left in project $OLD_PROJECT"
fi

# ── 3. volumes ──────────────────────────────────────────────────────────────
step "3. Copy volumes ${OLD_PROJECT}_<key> -> ${NEW_PROJECT}_<key> (old ones are kept)"
VOLUMES=("${REQUIRED_VOLUMES[@]}" "${OPTIONAL_VOLUMES[@]}")
[ "$SKIP_CACHES" = 1 ] || VOLUMES+=("${CACHE_VOLUMES[@]}")
[ "$SKIP_CACHES" = 1 ] && st SKIP "caches (${CACHE_VOLUMES[*]}) — recreated on boot"
compose_version="$(docker compose version --short 2>/dev/null || echo unknown)"
for key in "${VOLUMES[@]}"; do
  old="${OLD_PROJECT}_${key}" new="${NEW_PROJECT}_${key}" marker="$STATE_DIR/volume-${key}.done"
  if ! exists_volume "$old"; then
    st SKIP "$old does not exist"
    continue
  fi
  if exists_volume "$new" && [ -f "$marker" ]; then
    st DONE "$new already copied and verified ($(cat "$marker"))"
    continue
  fi
  if exists_volume "$new"; then
    if [ -n "$(docker run --rm -v "$new":/v:ro "$HELPER_IMAGE" sh -c 'ls -A /v' 2>/dev/null)" ]; then
      block "$new exists, is not empty and was not created by this script — inspect it; not overwriting"
      continue
    fi
    st TODO "$new exists but is empty — copying into it"
  else
    st TODO "create $new and copy $old into it"
    run docker volume create --label "com.docker.compose.project=$NEW_PROJECT" \
      --label "com.docker.compose.volume=$key" --label "com.docker.compose.version=$compose_version" "$new"
  fi
  cmd "docker run --rm -v $old:/from:ro -v $new:/to $HELPER_IMAGE sh -c 'cp -a /from/. /to/'"
  if [ "$APPLY" = 1 ] && [ "$BLOCKERS" = 0 ]; then
    docker run --rm -v "$old":/from:ro -v "$new":/to "$HELPER_IMAGE" sh -c 'cp -a /from/. /to/'
    a="$(volume_fingerprint "$old")" b="$(volume_fingerprint "$new")"
    if [ "${a%% *}" != "${b%% *}" ]; then
      echo "verification failed for $new: files old=${a%% *} new=${b%% *}" >&2; exit 1
    fi
    printf 'files=%s kib_old=%s kib_new=%s at=%s\n' "${a%% *}" "${a##* }" "${b##* }" "$(date -Is)" > "$marker"
    st DONE "$new verified: $(cat "$marker")"
  fi
done

# ── 4. images (the launcher runs `up --no-build`) ───────────────────────────
step "4. Tag images ${OLD_PROJECT}-<svc>:latest -> ${NEW_PROJECT}-<svc>:latest (old tags kept)"
for svc in "${IMAGES[@]}"; do
  old="${OLD_PROJECT}-${svc}:latest" new="${NEW_PROJECT}-${svc}:latest"
  if ! docker image inspect "$old" >/dev/null 2>&1; then st SKIP "$old not present"; continue; fi
  if [ "$(docker image inspect -f '{{.Id}}' "$new" 2>/dev/null)" = "$(docker image inspect -f '{{.Id}}' "$old")" ]; then
    st DONE "$new -> same image as $old"; continue
  fi
  if docker image inspect "$new" >/dev/null 2>&1; then
    st INFO "$new exists and differs from $old — keeping $new (built after the rename)"; continue
  fi
  st TODO "tag $old as $new"; run docker tag "$old" "$new"
done

# ── 5. data dir ─────────────────────────────────────────────────────────────
step "5. Data dir backend/.deer-flow -> backend/.nova (+ symlink .deer-flow -> .nova)"
B="$REPO_ROOT/backend"
if [ -L "$B/.deer-flow" ] && [ "$(readlink "$B/.deer-flow")" = ".nova" ] && [ -d "$B/.nova" ]; then
  st DONE "backend/.nova in place, backend/.deer-flow -> .nova"
elif [ -d "$B/.deer-flow" ] && [ ! -L "$B/.deer-flow" ]; then
  if [ -e "$B/.nova" ]; then
    if [ -d "$B/.nova" ] && [ -z "$(ls -A "$B/.nova")" ]; then
      st TODO "remove the empty backend/.nova first"; run rmdir "$B/.nova"
    else
      block "both backend/.deer-flow and a non-empty backend/.nova exist — merge them by hand"
    fi
  fi
  if [ "$BLOCKERS" = 0 ] || [ "$APPLY" = 0 ]; then
    st TODO "move the directory and leave a relative symlink (works on the host and inside the container's /app/backend bind mount)"
    run mv "$B/.deer-flow" "$B/.nova"
    run ln -s .nova "$B/.deer-flow"
  fi
elif [ -d "$B/.nova" ] && [ ! -e "$B/.deer-flow" ]; then
  st TODO "backend/.nova exists, compat symlink missing"; run ln -s .nova "$B/.deer-flow"
elif [ ! -e "$B/.deer-flow" ] && [ ! -e "$B/.nova" ]; then
  st SKIP "no data dir yet (fresh host)"
else
  block "unexpected state: $(ls -ld "$B/.deer-flow" "$B/.nova" 2>&1 | tr '\n' ' ')"
fi

# ── 6. networks ─────────────────────────────────────────────────────────────
step "6. Legacy networks (removed only when empty; '$NEW_NETWORK' reuses subnet $SUBNET)"
for net in "${OLD_NETWORKS[@]}"; do
  if ! exists_network "$net"; then st DONE "$net gone"; continue; fi
  attached="$(docker network inspect -f '{{range .Containers}}{{.Name}} {{end}}' "$net")"
  if [ "$APPLY" = 0 ] && [ -n "${attached// /}" ]; then
    # Dry-run: step 2 has not really run. Containers of the legacy project go
    # away with its `down` (which also removes this network); anything else
    # would still be attached and block the subnet.
    others=""
    for c in $attached; do
      [ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$c" 2>/dev/null)" = "$OLD_PROJECT" ] || others="$others$c "
    done
    attached="$others"
    if [ -z "${attached// /}" ]; then
      st PLAN "$net: only $OLD_PROJECT containers attached — removed by step 2's down (else: docker network rm $net)"
      continue
    fi
  fi
  if [ -n "${attached// /}" ]; then
    block "$net still has containers attached: $attached— stop them first (the new network needs the subnet)"
  else
    st TODO "$net is empty"; run docker network rm "$net"
  fi
done

if [ "$APPLY" = 1 ] && [ "$BLOCKERS" -gt 0 ]; then
  say ""; say "Stopped at a blocker; nothing after this point was done. Fix it and re-run (the steps above are idempotent)."
  say "To go back instead: $0 --rollback-help"
  exit 1
fi

# ── 7. start on the new names ───────────────────────────────────────────────
step "7. Re-register and start the PM2 app on scripts/pm2-nova.sh"
cur="$(pm2 jlist 2>/dev/null | python3 -c "import json,sys
print(next((p['pm2_env'].get('pm_exec_path','') for p in json.load(sys.stdin) if p['name']==sys.argv[1]),''))" "$PM2_APP" 2>/dev/null || true)"
if [ "$cur" = "$REPO_ROOT/scripts/pm2-nova.sh" ] && [ "$(pm2_status "$PM2_APP")" = "online" ]; then
  st DONE "$PM2_APP online on scripts/pm2-nova.sh"
else
  st TODO "$PM2_APP currently: ${cur:-absent} ($(pm2_status "$PM2_APP"))"
  [ -n "$cur" ] && run pm2 delete "$PM2_APP"
  run pm2 start "$REPO_ROOT/ecosystem.config.js" --only "$PM2_APP"
  run pm2 save
fi
for app in "${PM2_SIDECARS[@]}"; do
  st TODO "restart $app on the new code"; run pm2 restart "$app" --update-env
done

# ── 8. postflight ───────────────────────────────────────────────────────────
step "8. Postflight"
if [ "$APPLY" = 0 ]; then
  st PLAN "wait (<= ${HEALTH_TIMEOUT}s) for: ${NEW_PREFIX}-postgres healthy, gateway /health 200, site :2026 2xx/3xx"
  cmd "curl -fsS http://127.0.0.1:8001/health ; curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:2026/"
  st PLAN "compare per-table row counts in ${NEW_PREFIX}-postgres against $COUNTS_FILE (new >= old)"
  st PLAN "check project label: docker ps --filter label=com.docker.compose.project=$NEW_PROJECT"
else
  deadline=$((SECONDS + HEALTH_TIMEOUT)) ok_gw=0 ok_site=0
  while [ $SECONDS -lt $deadline ]; do
    curl -fsS -m 5 http://127.0.0.1:8001/health >/dev/null 2>&1 && ok_gw=1
    code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:2026/ 2>/dev/null || true)"
    case "$code" in 2??|3??) ok_site=1 ;; esac
    [ $ok_gw = 1 ] && [ $ok_site = 1 ] && break
    sleep 10
  done
  [ $ok_gw = 1 ] && st OK "gateway /health" || st FAIL "gateway /health did not answer within ${HEALTH_TIMEOUT}s"
  [ $ok_site = 1 ] && st OK "site :2026 ($code)" || st FAIL "site :2026 not serving (last code: ${code:-none})"
  fails=0
  if [ -s "$COUNTS_FILE" ] && running_container "$NEW_PG"; then
    while IFS=$'\t' read -r t before; do
      after="$(psql_in "$NEW_PG" "select count(*) from $t" 2>/dev/null || echo MISSING)"
      if [ "$after" = "MISSING" ] || [ "$after" -lt "$before" ]; then
        st FAIL "$t: before=$before after=$after"; fails=$((fails + 1))
      fi
    done < "$COUNTS_FILE"
    [ $fails = 0 ] && st OK "all $(wc -l < "$COUNTS_FILE") tables have >= their pre-cutover row count"
  else
    st FAIL "cannot compare row counts (counts file or ${NEW_PG} missing)"; fails=1
  fi
  n="$(docker ps -q --filter "label=com.docker.compose.project=$NEW_PROJECT" | wc -l)"
  st INFO "$n container(s) running in project $NEW_PROJECT"
  if [ $ok_gw = 0 ] || [ $ok_site = 0 ] || [ $fails -gt 0 ]; then
    say ""; say "POSTFLIGHT FAILED. Investigate (pm2 logs $PM2_APP), or roll back:"; rollback_help; exit 1
  fi
fi

# ── 9. later cleanup (never done by this script) ────────────────────────────
step "9. Later, once the new stack has run cleanly for a while (NOT done by this script)"
cmd "docker volume rm $(printf "${OLD_PROJECT}_%s " "${REQUIRED_VOLUMES[@]}" "${OPTIONAL_VOLUMES[@]}" "${CACHE_VOLUMES[@]}")"
cmd "docker image rm $(printf "${OLD_PROJECT}-%s:latest " "${IMAGES[@]}")"
cmd "rm backend/.deer-flow   # the compat symlink, after one release"
say ""
rollback_help
say ""
if [ "$APPLY" = 1 ]; then say "Cutover complete."; else say "Dry-run complete: $BLOCKERS blocker(s)."; fi
