// PM2 supervisor for the nova (formerly DeerFlow) Docker dev stack.
//
// A single foreground `docker compose up` (no -d) so PM2 owns the lifecycle and
// restarts the stack on crash/reboot. This MUST match the running `deerflow`
// process and include the dood overlay so AioSandboxProvider gets the host
// Docker socket — without it, AIO loses /var/run/docker.sock after a reboot or
// `pm2 restart` and per-thread sandbox containers fail to start.
//
// To (re-)register after editing (restarts the whole stack — do when idle):
//   pm2 delete deerflow && pm2 start ecosystem.config.js --only deerflow && pm2 save
module.exports = {
  apps: [
    {
      name: "deerflow",
      // Absolute path + interpreter:none so PM2 execs docker directly instead of
      // trying to require() it as a Node module.
      // Run via a wrapper shell script so DEER_FLOW_ROOT and cwd are set
      // explicitly before exec'ing docker compose. PM2's `env:` block isn't
      // reliably inherited by docker compose subprocesses — using a wrapper
      // makes the env deterministic regardless of PM2 version or fork mode.
      script: "/home/jahanzaib/Desktop/nova/scripts/pm2-deerflow.sh",
      interpreter: "none",
      args: "",
      cwd: "/home/jahanzaib/Desktop/nova",
      env: {
        DEER_FLOW_ROOT: "/home/jahanzaib/Desktop/nova",
      },
      autorestart: true,
      // Boot is slow (~90s for compose + VRAM cold start); cap the backoff so
      // a misconfigured stack doesn't loop forever, and explicitly bound the
      // restart count so a hard config error stays red in `pm2 list` instead
      // of silently crash-looping like the old setup did.
      max_restarts: 10,
      restart_delay: 5000,
      exp_backoff_restart_delay: 60,
      kill_timeout: 10000,
    },
    {
      // Exposes llama-server (127.0.0.1:8081) on the docker bridge IP
      // (172.17.0.1:8081) so Nova's container can reach it via
      // `host.docker.internal:8081`. Honors the project's "never bind to
      // 0.0.0.0" rule — only the bridge IP, not LAN. Started before
      // `deerflow` so it's already accepting connections when the gateway
      // boots; ECONNREFUSED on first client is naturally retried by the
      // bridge's per-connection forward.
      name: "llama-bridge",
      // Path is operator-configured via LOCAL_LLM_BRIDGE_SCRIPT (default
      // points at the standalone llama-bridge repo at ~/Desktop/llama-bridge/).
      // This keeps ecosystem.config.js generic — the bridge source lives
      // in its own repo, not here.
      script: process.env.LOCAL_LLM_BRIDGE_SCRIPT || "/home/jahanzaib/Desktop/llama-bridge/llama_bridge.py",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 2000,
      out_file: "/home/jahanzaib/.pm2/logs/llama-bridge-out.log",
      error_file: "/home/jahanzaib/.pm2/logs/llama-bridge-error.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    },
    {
      // Watchdog for the whole stack — probes 9 things every 30s and
      // auto-fixes known-broken cases (binary attestation drift, missing
      // docker containers). Its own pm2 status is the operator's single
      // pane of glass: green = everything healthy, red = probe failed and
      // auto-fix did not recover, yellow = something is masked (e.g.
      // upstream gateway cold).
      name: "nova-healthcheck",
      script: "/home/jahanzaib/Desktop/nova/scripts/healthcheck-daemon.py",
      interpreter: "none",
      args: "--interval 30",
      autorestart: true,
      max_restarts: 5,
      restart_delay: 10000,
      out_file: "/home/jahanzaib/.pm2/logs/nova-healthcheck-out.log",
      error_file: "/home/jahanzaib/.pm2/logs/nova-healthcheck-error.log",
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
