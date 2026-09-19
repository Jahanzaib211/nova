// PM2 supervisor for the nova (formerly DeerFlow) Docker dev stack.
//
// A single foreground `docker compose up` (no -d) so PM2 owns the lifecycle and
// restarts the stack on crash/reboot. This MUST match the running `deerflow`
// process and include the dood overlay so AioSandboxProvider gets the host
// Docker socket — without it, AIO loses /var/run/docker.sock after a reboot or
// `pm2 restart` and per-thread sandbox containers fail to start.
//
// To (re-)register after editing (restarts the whole stack — do when idle):
//   pm2 delete nova && pm2 start ecosystem.config.js --only nova && pm2 save
//
// 2026-08-13 — removed four apps that could never start. nova-healthcheck
// diffs this file against the live pm2 list and tries to "heal" anything
// missing, so every unstartable entry here became an infinite repair loop
// (~281 failed auto-fix actions/minute, each spawning a fresh Node process)
// that exhausted RAM + swap and froze the machine on 2026-08-12:
//   llama-bridge-disabled — script ~/Desktop/llama-bridge/ does not exist
//   nova-dify             — script ~/Desktop/dify/ does not exist
//   nova-tunnel           — wraps cloudflared-nova.service, which is not
//                           installed; nova.alilabsx.com is served by the
//                           `tunnel-nova` pm2 app instead
//   nova-monitoring       — Grafana/Prometheus/Loki duplicate the k3s
//                           monitoring namespace; none were running
// Anything added back here MUST be startable, or it becomes a repair loop.
module.exports = {
  apps: [
    {
      name: "nova",
      // Absolute path + interpreter:none so PM2 execs docker directly instead of
      // trying to require() it as a Node module.
      // Run via a wrapper shell script so DEER_FLOW_ROOT and cwd are set
      // explicitly before exec'ing docker compose. PM2's `env:` block isn't
      // reliably inherited by docker compose subprocesses — using a wrapper
      // makes the env deterministic regardless of PM2 version or fork mode.
      script: `${require("path").resolve(__dirname, "scripts/pm2-deerflow.sh")}`,
      interpreter: "none",
      args: "",
      cwd: __dirname,
      env: {
        DEER_FLOW_ROOT: __dirname,
      },
      autorestart: true,
      // Boot is slow (~90s for compose + VRAM cold start); cap the backoff so
      // a misconfigured stack doesn't loop forever, and explicitly bound the
      // restart count so a hard config error stays red in `pm2 list` instead
      // of silently crash-looping like the old setup did.
      max_restarts: 10,
      restart_delay: 5000,
      exp_backoff_restart_delay: 60,
      // 2026-09 hardening: bump kill timeout from 10s → 30s. The compose
      // stack takes ~90s to cold-boot (uv sync + frontend `pnpm install`
      // in dev), and the old 10s window meant a rolling restart could
      // force-kill the old stack before the new one bound its ports,
      // surfacing as a brief nginx 502. 30s gives clean SIGTERM → cascade
      // → exit without forcing the issue mid-boot.
      kill_timeout: 30000,
    },
    {
      // LiteLLM proxy — Nova's unified OpenAI-compatible model gateway.
      // Routes to the Ollama daemon (127.0.0.1:11434) and its free cloud
      // models; add more providers in docker/litellm/config.yaml. Bound to
      // the docker bridge IP only (same "never 0.0.0.0" rule as llama-bridge)
      // so Nova's container reaches it at host.docker.internal:4000.
      // Watchdog probe P10_litellm auto-heals this app.
      name: "nova-litellm",
      script: `${require("path").resolve(__dirname, "scripts/pm2-litellm.sh")}`,
      interpreter: "none",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      kill_timeout: 8000,
      out_file: `${require("path").join(require("os").homedir(), ".pm2/logs/nova-litellm-out.log")}`,
      error_file: `${require("path").join(require("os").homedir(), ".pm2/logs/nova-litellm-error.log")}`,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    },
    {
      // Forwards loopback-only host services (OpenClaw 18789, Mailcow API 8080,
      // Chatwoot 4800, Twenty 3008) from the docker bridge IP to 127.0.0.1 so
      // Nova's containers reach them at host.docker.internal:<port>. Bound to
      // 172.17.0.1 only — same "never 0.0.0.0" rule as nova-litellm; upstream
      // auth (API keys/tokens) is untouched. Watchdog probe P16_host_bridge
      // auto-heals this app. Ports: NOVA_HOST_BRIDGE_PORTS in the script.
      name: "nova-host-bridge",
      script: `${require("path").resolve(__dirname, "scripts/host-bridge.sh")}`,
      interpreter: "none",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      kill_timeout: 5000,
      out_file: `${require("path").join(require("os").homedir(), ".pm2/logs/nova-host-bridge-out.log")}`,
      error_file: `${require("path").join(require("os").homedir(), ".pm2/logs/nova-host-bridge-error.log")}`,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    },
    {
      // Keeps the Nova Ops gate status files fresh.
      //
      // The gate producers write JSON that the operator console reads. Without
      // a supervisor they only ran when something invoked them by hand, so the
      // console showed "Stale — produced 10h ago" on real-but-outdated numbers,
      // which reads as authoritative and is not. nova-healthcheck already had
      // this treatment and was correspondingly always fresh; this extends it to
      // the host, drift and CI gates.
      //
      // Cadence lives in the daemon (host 5m, drift 15m, ci 6h) and is
      // overridable via NOVA_GATE_*_INTERVAL below.
      name: "nova-gates",
      script: `${require("path").resolve(__dirname, "scripts/gates/gates-daemon.py")}`,
      interpreter: "none",
      autorestart: true,
      max_restarts: 5,
      restart_delay: 15000,
      out_file: `${require("path").join(require("os").homedir(), ".pm2/logs/nova-gates-out.log")}`,
      error_file: `${require("path").join(require("os").homedir(), ".pm2/logs/nova-gates-error.log")}`,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      env: {
        NOVA_GATE_HOST_INTERVAL: "300",
        NOVA_GATE_DRIFT_INTERVAL: "900",
        NOVA_GATE_CI_INTERVAL: "21600",
      },
    },
    {
      // Watchdog for the whole stack — probes 11 things every 30s and
      // auto-fixes known-broken cases (binary attestation drift, missing
      // docker containers). Its own pm2 status is the operator's single
      // pane of glass: green = everything healthy, red = probe failed and
      // auto-fix did not recover, yellow = something is masked (e.g.
      // upstream gateway cold).
      name: "nova-healthcheck",
      script: `${require("path").resolve(__dirname, "scripts/healthcheck-daemon.py")}`,
      interpreter: "none",
      args: "--interval 30",
      autorestart: true,
      max_restarts: 5,
      restart_delay: 10000,
      out_file: `${require("path").join(require("os").homedir(), ".pm2/logs/nova-healthcheck-out.log")}`,
      error_file: `${require("path").join(require("os").homedir(), ".pm2/logs/nova-healthcheck-error.log")}`,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      // Generic — these env vars configure probe targets and the optional
      // binary-attestation probe. Operators can override per-deployment
      // (e.g. point at a different gateway host/port). The watchdog code
      // doesn't know about specific services.
      env: {
        LOCAL_LLM_GATEWAY_HOST: "127.0.0.1",
        LOCAL_LLM_GATEWAY_PORT: "9000",
        LLAMA_HOST: "127.0.0.1",
        LLAMA_BRIDGE_HOST: "172.17.0.1",
        LITELLM_HOST: "172.17.0.1",
        LITELLM_PORT: "4000",
        DIFY_HOST: "127.0.0.1",
        DIFY_PORT: "8088",
        // Probes for services that have been retired or are intentionally
        // not wired on this deployment. Left enabled they sit RED forever,
        // which both trains the operator to ignore this dashboard and —
        // before the repair circuit breaker existed — drove an endless
        // auto-fix retry storm:
        //   P4_local_llm_gateway — local gateway on :9000 is not running;
        //                          Nova routes everything through LiteLLM
        //                          and the cloud providers (see SESSION-
        //                          HANDOFF §3.G4). Wired = config-only
        //                          change, but not on the production path.
        //   P5_llama_loopback    — llama.cpp on :8081 is the local-llama
        //                          fall-back; not used by Nova here either.
        //   P6_llama_vram        — same dead :8081 as P5, and it was left
        //                          out of this list when P5 was disabled.
        //                          Nothing of Nova's listens there now, but
        //                          another project's `hsproxy` container
        //                          does, so the probe was reading a
        //                          payments proxy's error body and calling
        //                          it "no model advertised" — a permanent
        //                          yellow describing a service Nova does
        //                          not own. Nova's local models go through
        //                          Ollama on :11434 via nova-litellm.
        //   P9_bridge            — llama-bridge; ~/Desktop/llama-bridge
        //                          does not exist
        //   P11_dify             — nova-dify; ~/Desktop/dify does not exist
        // P12_tunnel is ENABLED again: it demanded cloudflared-nova.service,
        // which was never installed here, so it sat permanently red and got
        // switched off — leaving P1/P2/P3 all terminating at localhost:2026 and
        // nothing at all watching the public hostname. The probe now accepts
        // the `tunnel-nova` pm2 app as the connector, which is how this host
        // actually runs it, and its layer-2 check reaches the Cloudflare edge.
        // Re-enable by removing a name here once the service is back.
        HEALTHCHECK_DISABLED_PROBES:
          "P4_local_llm_gateway,P5_llama_loopback,P6_llama_vram,P9_bridge,P11_dify",
        // Binary-attestation probe — left unset by default. To enable:
        //   WATCHDOG_ATTESTATION_BINARY_PATH=/path/to/binary
        //   WATCHDOG_ATTESTATION_CONSTITUTION_PATH=/path/to/constitution
        //   WATCHDOG_ATTESTATION_HASH_DIR=/tmp/ali-ram-moat
        //   WATCHDOG_ATTESTATION_RESTART_CMD="sudo systemctl restart my-svc"
        HEALTHCHECK_CYCLE_DEADLINE_SEC: "60",
      },
    },
  ],
};
