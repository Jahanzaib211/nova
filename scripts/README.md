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

## Also see

- `Makefile` at repo root — `make dev`, `make stop`, `make up`
- `backend/Makefile` — backend-only commands
