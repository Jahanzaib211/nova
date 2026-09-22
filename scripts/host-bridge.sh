#!/bin/bash
# PM2 wrapper for nova-host-bridge (see ecosystem.config.js).
#
# Nova's containers cannot see host services that bind 127.0.0.1 only —
# Mailcow's admin API (:8080), Chatwoot (:4800), Twenty CRM (:3008) and the
# OpenClaw gateway (:18789) are all loopback-bound, so host.docker.internal
# resolves and then the connection is refused. This app forwards each port
# from the docker bridge IP (172.17.0.1, the same "never 0.0.0.0" rule as
# nova-litellm) to loopback, one socat per port, so the gateway reaches them
# at host.docker.internal:<port> and nothing on the LAN can.
#
# Auth is unchanged: every upstream still requires its own API key/token.
# Watchdog probe P16_host_bridge checks the first port and heals via pm2.
#
#   NOVA_HOST_BRIDGE_PORTS  space-separated ports (default: 18789 8080 4800 3008)
#   NOVA_HOST_BRIDGE_BIND   listen address (default: 172.17.0.1)
set -eu

BIND="${NOVA_HOST_BRIDGE_BIND:-172.17.0.1}"
PORTS="${NOVA_HOST_BRIDGE_PORTS:-18789 8080 4800 3008}"
SOCAT="${NOVA_SOCAT_BIN:-/usr/bin/socat}"

case "$BIND" in
  0.0.0.0|::|"") echo "refusing to bind '$BIND' — LAN exposure" >&2; exit 2 ;;
esac
[ -x "$SOCAT" ] || { echo "socat not found at $SOCAT (apt install socat)" >&2; exit 2; }

pids=""
cleanup() {
  # shellcheck disable=SC2086
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
}
trap 'cleanup; exit 0' INT TERM

for port in $PORTS; do
  "$SOCAT" "TCP-LISTEN:${port},bind=${BIND},fork,reuseaddr" "TCP:127.0.0.1:${port}" &
  pids="$pids $!"
  echo "host-bridge: ${BIND}:${port} -> 127.0.0.1:${port}" >&2
done

# Exit when any forwarder dies so pm2 restarts the set as a unit.
wait -n
cleanup
exit 1
