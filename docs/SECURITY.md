# Security

Nova's security architecture covers authentication, authorization, secrets management, CSP, and sandbox isolation.

## Authentication

### Architecture

- **AuthMiddleware** validates JWT tokens on every request (except public endpoints)
- **CSRF protection** via double-submit cookie pattern
- **Session management** with token versioning (revoke-all via `token_version` bump)
- **BYOK** (Bring Your Own Key) for per-user API key management

### Auth Modes

| Mode | Description |
|---|---|
| **No-auth** | `auth.enabled: false` — all requests treated as `default` user |
| **Local auth** | Built-in auth with user registration, login, password reset |
| **SSO** | External identity provider integration |

### Key Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/auth/login` | POST | User login |
| `/api/auth/register` | POST | User registration |
| `/api/auth/logout` | POST | User logout |
| `/api/auth/change-password` | POST | Password change |
| `/api/v1/admin/*` | Various | Ops console (admin-only) |

### Admin Ops Console

Gated by `require_admin_user`. Every endpoint reads from SQL-backed `database.backend`. Audit trail (`admin_audit`) is append-only and captures actor, IP, action, target, and payload.

## Authorization

### Middleware Chain

1. **AuthMiddleware** — JWT validation, user resolution
2. **CSRFMiddleware** — Double-submit cookie validation
3. **GuardrailMiddleware** — Pre-tool-call authorization via pluggable `GuardrailProvider`
4. **SandboxAuditMiddleware** — Audits sandboxed shell/file operations

### Guardrails

Guardrails authorize tool calls before execution. Three provider options:

1. **AllowlistProvider** (zero deps) — static tool allowlist
2. **OAP policy providers** — external policy engine
3. **Custom providers** — implement `GuardrailProvider` protocol

See `backend/docs/GUARDRAILS.md` for setup and usage.

## Secrets Management

### Environment Variables

Secrets are resolved from environment variables at runtime:

```yaml
models:
  - api_key: $OPENAI_API_KEY  # resolved from env
```

### Secrets Tools

```bash
scripts/secrets-doctor.sh    # Check secrets health
scripts/secrets-export.sh    # Export secrets
```

### BYOK (Bring Your Own Key)

Users can provide their own API keys via the settings UI. Keys are encrypted at rest. Admin can list/revoke user BYOK keys via ops console.

## Content Security Policy (CSP)

### Default Policy

```
default-src 'self';
script-src 'self' 'unsafe-inline' 'unsafe-eval';
style-src 'self' 'unsafe-inline';
img-src 'self' blob: data: https:;
font-src 'self' data:;
connect-src 'self' ws: wss:;
frame-src 'self' blob:;
media-src 'self' blob: data:;
sandbox allow-scripts allow-same-origin;
```

### Key Points

- `X-Frame-Options: DENY` on API responses (prevents framing)
- `Content-Security-Policy: sandbox allow-scripts allow-same-origin` on proxied responses
- Cookies stripped from proxied responses
- Active content types (`text/html`, `image/svg+xml`) forced as download attachments

### nginx Configuration

All four nginx configs (docker, tls, local, k8s) carry identical CSP headers. Dedicated locations for sandbox preview proxy and artifact responses re-add only `nosniff` to avoid breaking iframes.

## Sandbox Isolation

### Local Sandbox

- Per-thread isolation: `backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/`
- Virtual paths: `/mnt/user-data/{workspace,uploads,outputs}`
- Path traversal protection: rejects paths outside `/mnt/user-data/`

### Docker Sandbox (AIO)

- Container-based isolation
- Docker-in-Docker (DooD) for container management
- Volume mounts for workspace access
- Network isolation

### Tool Security

- `str_replace` uses atomic temp+rename for local sandboxes
- `download_file` enforces size limit via streaming chunked reads
- `write_file` enforces 2 MB hard max, 200 KB auto-chunk threshold
- Path validation on all file operations

## Rate Limiting

- **Login attempt rate limiter** — in-process, prevents brute force
- **Preflight quota middleware** — quota-based rate limiting
- **API rate limiting** — configurable per-endpoint

## Audit Trail

- Admin ops console actions logged to `admin_audit` table
- Auth events (login, logout, password change) logged
- Channel configuration changes logged
- Self-service credit requests logged

## Security Testing

```bash
# Run security tests
cd backend && PYTHONPATH=. uv run pytest tests/test_sandbox_tools_security.py -v

# Run auth tests
cd backend && PYTHONPATH=. uv run pytest tests/test_auth*.py -v

# Run guardrail tests
cd backend && PYTHONPATH=. uv run pytest tests/test_guardrails*.py -v

# Run cross-reference check (no forbidden strings)
python3 backend/tests/test_no_cross_references.py
```

## Key Files

- `backend/app/gateway/middleware/` — Auth, CSRF, guardrails, audit middleware
- `backend/docs/AUTH_DESIGN.md` — Detailed auth architecture
- `backend/docs/GUARDRAILS.md` — Guardrail setup and provider protocol
- `backend/docs/AUTH_TEST_PLAN.md` — Auth test coverage
- `backend/tests/test_sandbox_tools_security.py` — Sandbox security tests
