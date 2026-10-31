# Security Policy

## Supported Versions

Use the latest tagged release (`v7.5.0` or later) for security updates.
Check the [releases page](https://github.com/Jahanzaib211/nova/releases) for the most recent stable version.

## Reporting a Vulnerability

Please email security concerns to `alilabsx@gmail.com` or open a private security advisory at
<https://github.com/Jahanzaib211/nova/security/advisories/new>.

Please do **not** file public issues for security vulnerabilities.

## Deployment notes

- **Host bridge (`nova-host-bridge`, `scripts/host-bridge.sh`).** Forwards a
  fixed list of loopback-only host ports (OpenClaw 18789, Mailcow API 8080,
  Chatwoot 4800, Twenty 3008) from the docker bridge IP `172.17.0.1` to
  `127.0.0.1` so Nova's containers can reach them. It refuses to bind
  `0.0.0.0`; nothing on the LAN gains access, and every upstream still
  enforces its own API key or token.
- **ACP agents (`NOVA_ACP_AGENTS=1`).** Opt-in. Mounts `~/.claude` read-only
  into the gateway for the Claude Code binary and the OpenClaw gateway token
  from `~/.nova/secrets/openclaw_token` (0600). Permission requests from
  agents are denied by default and opened per tool kind with
  `permission_policy` (see `backend/docs/ACP_AGENTS.md`). The gateway itself
  never reads those credentials.
