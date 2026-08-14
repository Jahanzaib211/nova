# Troubleshooting

Quick-reference for common failure modes and fixes.

## Quick Diagnostics

```bash
# Full system check
scripts/doctor.py

# Docker status
scripts/docker.sh status

# Gateway health
curl -s http://localhost:2026/health | python3 -m json.tool

# Backend tests
cd backend && make test

# Frontend tests
cd frontend && pnpm test

# Cross-reference check
python3 backend/tests/test_no_cross_references.py
```

## Common Issues

### Gateway won't start

**Symptom**: `make dev` fails or gateway health returns unhealthy.

```bash
# Check if port 8001 is in use
lsof -i :8001

# Check Docker socket
ls -la /var/run/docker.sock

# Check gateway logs
docker logs deer-flow-gateway

# Restart gateway
scripts/docker.sh restart
```

### Sandbox not available

**Symptom**: Agent returns "requires a per-thread sandbox" or "sandbox not available".

```bash
# Check sandbox mode in config.yaml
grep -A5 "sandbox:" config.yaml

# For local sandbox, check user data directory
ls -la backend/.deer-flow/users/

# For Docker sandbox, check container
docker ps | grep sandbox
docker logs deer-flow-gateway | grep sandbox
```

### Frontend can't connect to backend

**Symptom**: White page, API errors, or "Failed to fetch".

```bash
# Check nginx is running
curl -s http://localhost:2026/health

# Check frontend dev server
curl -s http://localhost:3000

# Check backend directly
curl -s http://localhost:8001/health

# Verify nginx config
nginx -t -c docker/nginx/nginx.conf
```

### Voice not working

**Symptom**: Microphone button doesn't work, no audio playback.

```bash
# Check voice is enabled
grep -A5 "speech:" config.yaml

# Check voice weights exist
ls -la ~/.cache/nova/voice/

# Test voice settings API
curl -s http://localhost:2026/api/voice/config | python3 -m json.tool
```

See `docs/VOICE.md` for detailed voice troubleshooting.

### Tests failing

```bash
# Run full test suite with verbose output
cd backend && make test 2>&1 | tail -20

# Run specific failing test
cd backend && PYTHONPATH=. uv run pytest tests/test_<name>.py -v

# Check for circular imports
cd backend && PYTHONPATH=. python -c "from deerflow.sandbox.tools import str_replace_tool"

# Run blocking IO gate
cd backend && make test-blocking-io
```

### Docker issues

```bash
# Clean up orphaned containers
scripts/cleanup-containers.sh

# Full Docker reset
scripts/docker.sh stop
docker system prune -f
scripts/docker.sh start

# Check Docker Compose config
docker compose -f docker/docker-compose.yaml config
```

### Port conflicts

| Port | Service | Fix |
|---|---|---|
| 2026 | Nginx | `lsof -i :2026` → kill process |
| 3000 | Frontend | `lsof -i :3000` → kill process |
| 8001 | Gateway | `lsof -i :8001` → kill process |
| 8002 | Provisioner | `lsof -i :8002` → kill process |

### Config issues

```bash
# Validate config schema
cd backend && PYTHONPATH=. python -c "from deerflow.config import get_app_config; get_app_config()"

# Auto-upgrade config
make config-upgrade

# Check config version
grep config_version config.yaml
```

### Memory issues

```bash
# Check memory status
curl -s http://localhost:2026/api/memory/status | python3 -m json.tool

# Reload memory
curl -s -X POST http://localhost:2026/api/memory/reload

# Check memory file
ls -la backend/.deer-flow/users/*/memory.json
```

### IM channel issues

```bash
# Check channel status
curl -s http://localhost:2026/api/channels/providers | python3 -m json.tool

# Check channel connections
curl -s http://localhost:2026/api/channels/connections | python3 -m json.tool

# Test channel config
grep -A10 "channels:" config.yaml
```

## Getting Help

- **Logs**: `docker logs deer-flow-gateway` (Docker) or terminal output (local)
- **Health**: `curl -s http://localhost:2026/health`
- **Tests**: `cd backend && make test`
- **Doctor**: `scripts/doctor.py`
