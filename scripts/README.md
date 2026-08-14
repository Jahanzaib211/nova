# scripts

Operational and development scripts for Nova.

## Key scripts

| Script | Description |
|---|---|
| `serve.sh` | Local daemon / foreground server (dev and prod modes) |
| `docker.sh` | Docker dev environment (start, stop, status, auto DooD overlay) |
| `deploy.sh` | Production deployment (Docker Compose) |
| `check.py` | System requirements checker |
| `config-upgrade.sh` | Auto-merge config.yaml schema changes |
| `fix_cross_references.py` | Auto-fix forbidden cross-project references |
| `scan_changed_blocking_io.py` | Diff-scoped blocking IO scan |
| `fetch-voice-models.sh` | Download voice model weights |

## Diagnostics & health

| Script | Description |
|---|---|
| `doctor.py` | Full system diagnostic (Docker, config, ports, models) |
| `healthcheck-daemon.py` | PM2 watchdog daemon (P1-P12 probes) |
| `check_no_cross_references.sh` | Shell wrapper for cross-ref validation |
| `check_platform_guardrails.py` | Platform guardrail checks |
| `check.sh` | General system checks |
| `detect_blocking_io_static.py` | Static AST blocking IO detection |
| `detect_thread_boundaries.py` | Thread boundary detection |
| `detect_uv_extras.py` | Detect required uv extras |
| `tool-error-degradation-detection.sh` | Tool error degradation detection |

## Setup & configuration

| Script | Description |
|---|---|
| `setup-sandbox.sh` | Sandbox environment setup |
| `setup-reboot-persistence.sh` | Reboot persistence setup |
| `setup_wizard.py` | Interactive setup wizard |
| `configure.py` | Configuration helper |
| `start-daemon.sh` | Start background daemon |
| `install-cloudflared-nova.sh` | Install Cloudflare tunnel |
| `wait-for-port.sh` | Wait for port to become available |

## Docker & containers

| Script | Description |
|---|---|
| `cleanup-containers.sh` | Clean up orphaned Docker containers |
| `sandbox_memory_profile.py` | Profile sandbox memory usage |

## Monitoring & PM2

| Script | Description |
|---|---|
| `pm2-deerflow.sh` | PM2 management for DeerFlow |
| `pm2-cloudflared-nova.sh` | PM2 management for Cloudflare tunnel |
| `pm2-litellm.sh` | PM2 management for LiteLLM proxy |
| `pm2-monitoring.sh` | PM2 monitoring setup |
| `nova-visitors.py` | Visitor analytics |

## Data & migration

| Script | Description |
|---|---|
| `load_memory_sample.py` | Load sample memory data |
| `sync_labels.py` | Sync GitHub labels |
| `secrets-doctor.sh` | Secrets health check |
| `secrets-export.sh` | Export secrets |
| `export_claude_code_oauth.py` | Export Claude Code OAuth tokens |

## Hackathon

| Script | Description |
|---|---|
| `hackathon-track3-prescreen.sh` | Hackathon track 3 pre-screening |
| `hackathon-verify-image.sh` | Hackathon image verification |
| `amd-serve-vllm.sh` | AMD vLLM serve helper |

## Also see

- `Makefile` at repo root — `make dev`, `make stop`, `make up`
- `backend/Makefile` — backend-only commands
