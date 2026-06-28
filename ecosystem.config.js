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
      script: "/usr/bin/docker",
      interpreter: "none",
      args:
        "compose " +
        "-f /home/jahanzaib/Desktop/nova/docker/docker-compose-dev.yaml " +
        "-f /home/jahanzaib/Desktop/nova/docker/docker-compose.dood.yaml " +
        // searxng/tor are optional iGIN0 services (disabled by default). They are
        // scaled to 0 because searxng was grabbing the gateway's pinned static IP
        // (192.168.200.3) and tor crash-loops under the non-root hardening
        // (setgid EPERM). Re-enable by removing these scales once those are fixed.
        "-p deer-flow-dev up --no-build --scale provisioner=0 --scale searxng=0 --scale tor=0",
      cwd: "/home/jahanzaib/Desktop/nova",
      env: {
        DEER_FLOW_ROOT: "/home/jahanzaib/Desktop/nova",
      },
      autorestart: true,
    },
  ],
};
