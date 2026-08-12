# Nova ops-console — God-mode API surface

Complete reference for every admin endpoint the Ali Technologies ops console
calls. All `/api/v1/admin/*` routes are gated by `require_admin_user()` and
reachable by any of:

- A real admin user session (`access_token` cookie with `system_role="admin"`).
- The ops service token (`X-Nova-Ops-Token` header matching `NOVA_OPS_TOKEN`).
- The internal service token (in-process channel workers).

Every mutating endpoint writes one row to `admin_audit` with the caller's
email, IP, User-Agent, and a kebab-case action name. The wire format
is JSON; timestamps are tz-aware ISO-8601 UTC (`…Z`).

The BFF (`/api/ops/*`) is a zero-config proxy from the Next.js console
that forwards everything below verbatim, so the canonical endpoint is the
backend on the **gateway** port. The BFF exists only so the
`NOVA_OPS_TOKEN` never reaches the browser.

---

## User roster

### `GET /api/v1/admin/users`

List registered users, newest first. Audited `view-users`.

| Query | Type | Default | Description |
|---|---|---|---|
| `limit` | int (1–200) | 50 | Page size |
| `offset` | int (≥0) | 0 | Page offset |

**Response**: `AdminUsersResponse{data: list[AdminUserRow], total, limit, offset, has_more}`.

`AdminUserRow`: `{id, email, system_role, plan, plan_status, created_at}`.

### `GET /api/v1/admin/users/stats`

Aggregate signup metrics. Audited `view-user-stats`.

**Response**: `AdminUserStats{total, by_plan: dict[str,int], new_last_7_days, new_last_30_days}`.

### `GET /api/v1/admin/users/recent`

Per-user live stats with last-sign-in + last-run. Audited `view-users-recent`.

| Query | Type | Default | Description |
|---|---|---|---|
| `limit` | int (1–200) | 20 | Page size |
| `offset` | int (≥0) | 0 | Page offset |

**Response**: `RecentUsersResponse{data: list[RecentUserRow], limit, offset}`.

`RecentUserRow`: `{id, email, system_role, plan, plan_status, created_at, last_sign_in_at, last_run_at, is_forbidden}`.

`last_sign_in_at` is stamped on every successful `POST /api/v1/auth/login/local`;
`last_run_at` is aggregated from the `runs` table via a single LEFT JOIN.

### `GET /api/v1/admin/users/{user_id}`

Per-user detail: profile + credit standing + recent runs. Audited
`view-user-detail` (sensitive — full profile + balance + runs).

**Response**: `AdminUserDetail` with `{id, email, system_role, plan, plan_status, created_at, last_sign_in_at, is_forbidden, referral_code, referred_by, referral_count, daily_limit_override, credit_usage_reset_at, daily_limit, used, remaining, bonus_daily_tokens, recent_runs}`.

### `PATCH /api/v1/admin/users/{user_id}/plan`

| Body | Type | Notes |
|---|---|---|
| `plan` | `"free"` \| `"plus"` \| `"enterprise"` | Sets `plan_status="admin_granted"` for non-free. |

Audited `set-plan`.

### `POST /api/v1/admin/users/{user_id}/reset-usage`

Reset the user's daily usage to full. Audited `reset-usage`.

### `POST /api/v1/admin/users/{user_id}/grant`

| Body | Type |
|---|---|
| `daily_bonus_tokens` | int (1–100,000,000) |
| `days` | int (1–365) |

Audited `grant-credits`.

### `PATCH /api/v1/admin/users/{user_id}/limit`

| Body | Type | Notes |
|---|---|---|
| `daily_limit_override` | int (0–1,000,000,000) \| null | null clears the override. |

Audited `set-limit`.

### `POST /api/v1/admin/reset-all-usage`

Bulk reset. Audited `reset-all-usage`.

### `POST /api/v1/admin/users/{user_id}/reset-password`

Generate a fresh password for a user. **Returns the one-time plaintext** in the response body — the operator must capture it before navigating away. The server keeps only the bcrypt hash.

Bumps `token_version` (invalidates every existing JWT), sets `needs_setup=True`.

Audit row: `reset-password`.

**Response**: `{email, new_password}`.

```bash
curl -X POST http://localhost:8001/api/v1/admin/users/$UID/reset-password \
  -H "X-Nova-Ops-Token: $NOVA_OPS_TOKEN" \
  -H "X-CSRF-Token: $(openssl rand -hex 24)" \
  -H "Cookie: csrf_token=…"
```

### `POST /api/v1/admin/users/{user_id}/revoke-sessions`

Sign the user out everywhere without changing the password. Bumps `token_version`.

Audit row: `revoke-sessions`.

### `POST /api/v1/admin/users/{user_id}/forbid`

Ban the user. The AuthMiddleware rejects login at the next attempt with
403 (not 401 — same shape the user sees for any wrong password, so the
toggle is not a probe oracle). Also bumps `token_version` so existing
sessions are invalidated.

Audit row: `forbid-user`.

### `POST /api/v1/admin/users/{user_id}/unforbid`

Reverse the forbid. The user can log in again.

Audit row: `unforbid-user`.

### `GET /api/v1/admin/users/{user_id}/sessions`

Auth history for a user (login, logout, change-password, update-email,
failed-login, reset-password, revoke-sessions, forbid-user, …).

| Query | Type | Default |
|---|---|---|
| `limit` | int (1–200) | 50 |

Audited `view-sessions`.

**Response**: `UserSessionsResponse{data: list[UserSessionEvent]}`.

`UserSessionEvent`: `{id, action, actor, payload, actor_ip, actor_user_agent, created_at}`.

### `GET /api/v1/admin/users/{user_id}/byok`

Read a user's BYOK state (encrypted key is never returned).

Audited `view-user-byok`.

**Response**: `{enabled, has_key, provider, created_at, updated_at}`.

### `DELETE /api/v1/admin/users/{user_id}/byok`

Wipe a user's stored BYOK key. Audited `revoke-user-byok`.

### `GET /api/v1/admin/users/{user_id}/channels`

List one user's IM channel connections (no raw credentials).
Audited `view-channel-connections`.

### `DELETE /api/v1/admin/users/{user_id}/channels/{connection_id}`

Tear down a single channel connection. Audited `revoke-channel-connection`.

### `GET /api/v1/admin/users/{user_id}/conversations`

List one user's threads. Audited `view-conversations`.

### `GET /api/v1/admin/users/{user_id}/conversations/{thread_id}/messages`

| Query | Type | Default |
|---|---|---|
| `limit` | int (≤200) | 50 |
| `before_seq` | int | latest |
| `after_seq` | int | earliest |

Verifies `thread_id` belongs to `user_id` first. Audited
`view-conversation-messages`.

### `POST /api/v1/admin/users/{user_id}/conversations/{thread_id}/runs/{run_id}/cancel`

| Query | Type | Default |
|---|---|---|
| `action` | `"interrupt"` \| `"rollback"` | `"interrupt"` |

Audited `cancel-user-run`.

---

## Credit requests (operator inbox)

### `GET /api/v1/admin/credit-requests`

| Query | Type | Default |
|---|---|---|
| `status_filter` | `"pending"` \| `"approved"` \| `"declined"` \| `"all"` | `"pending"` |
| `limit` | int (1–200) | 100 |
| `offset` | int (≥0) | 0 |

Audited `view-credit-requests`.

### `POST /api/v1/admin/credit-requests/{request_id}/resolve`

| Body | Type | Default |
|---|---|---|
| `approve` | bool | — |
| `daily_bonus_tokens` | int (1–100,000,000) | 250,000 |
| `days` | int (1–365) | 7 |

Audited `resolve-credit-request`.

---

## Activity + audit

### `GET /api/v1/admin/activity`

| Query | Type | Default |
|---|---|---|
| `limit` | int (1–200) | 50 |
| `offset` | int (≥0) | 0 |

Audited `view-activity`.

### `GET /api/v1/admin/audit`

| Query | Type | Default |
|---|---|---|
| `limit` | int (1–200) | 50 |
| `offset` | int (≥0) | 0 |
| `target_user_id` | str | all |

Audited `view-audit`.

---

## Channel runtime (admin)

### `POST /api/channels/{provider}/runtime-config`

Configure a channel provider's runtime credentials (bot tokens, app ids/secrets).
Audited `configure-channel-runtime`.

### `DELETE /api/channels/{provider}/runtime-config`

Stop a channel and revoke all user connections for that provider. Audited
`deconfigure-channel-runtime`.

---

## Infra (admin read-only)

### `GET /api/v1/admin/infra/pods`

List Pods in the provisioner's namespace. Audited `view-infra-pods`.

### `GET /api/v1/admin/infra/deployments`

List Deployments with rollout status. Audited `view-infra-deployments`.

### `GET /api/v1/admin/infra/events`

| Query | Type | Default |
|---|---|---|
| `limit` | int (1–500) | 100 |

Recent namespace Events, newest first. Audited `view-infra-events`.

### `GET /api/v1/admin/infra/metrics`

Pod + node CPU/memory usage. Audited `view-infra-metrics`.

### `GET /api/v1/admin/infra/pods/{pod_name}/logs`

| Query | Type | Default |
|---|---|---|
| `tail` | int (1–2000) | 200 |
| `container` | str \| null | default |

Audited `view-pod-logs`.

---

## Audit trail — every action reference

The full enumeration of action names that the gateway writes to
`admin_audit.action`. Use these to compose queries against
`/api/v1/admin/audit?target_user_id=…`.

| Action | Source |
|---|---|
| `login` | successful `POST /api/v1/auth/login/local` |
| `failed-login` | rejected login attempt (with the submitted email in `payload`) |
| `logout` | `POST /api/v1/auth/logout` |
| `register` | `POST /api/v1/auth/register` |
| `initialize` | `POST /api/v1/auth/initialize` (first admin) |
| `change-password` | self-service `POST /api/v1/auth/change-password` |
| `update-email` | self-service `POST /api/v1/auth/update-email` |
| `view-users` | `GET /api/v1/admin/users` |
| `view-user-stats` | `GET /api/v1/admin/users/stats` |
| `view-users-recent` | `GET /api/v1/admin/users/recent` |
| `view-user-detail` | `GET /api/v1/admin/users/{id}` |
| `view-sessions` | `GET /api/v1/admin/users/{id}/sessions` |
| `set-plan` | `PATCH /api/v1/admin/users/{id}/plan` |
| `reset-usage` | `POST /api/v1/admin/users/{id}/reset-usage` |
| `grant-credits` | `POST /api/v1/admin/users/{id}/grant` |
| `set-limit` | `PATCH /api/v1/admin/users/{id}/limit` |
| `reset-all-usage` | `POST /api/v1/admin/reset-all-usage` |
| `reset-password` | `POST /api/v1/admin/users/{id}/reset-password` |
| `revoke-sessions` | `POST /api/v1/admin/users/{id}/revoke-sessions` |
| `forbid-user` | `POST /api/v1/admin/users/{id}/forbid` |
| `unforbid-user` | `POST /api/v1/admin/users/{id}/unforbid` |
| `view-user-byok` | `GET /api/v1/admin/users/{id}/byok` |
| `revoke-user-byok` | `DELETE /api/v1/admin/users/{id}/byok` |
| `view-conversations` | `GET /api/v1/admin/users/{id}/conversations` |
| `view-conversation-messages` | `GET /api/v1/admin/users/{id}/conversations/{tid}/messages` |
| `view-channel-connections` | `GET /api/v1/admin/users/{id}/channels` |
| `revoke-channel-connection` | `DELETE /api/v1/admin/users/{id}/channels/{cid}` |
| `cancel-user-run` | `POST /api/v1/admin/users/{id}/conversations/{tid}/runs/{rid}/cancel` |
| `view-activity` | `GET /api/v1/admin/activity` |
| `view-audit` | `GET /api/v1/admin/audit` |
| `view-credit-requests` | `GET /api/v1/admin/credit-requests` |
| `resolve-credit-request` | `POST /api/v1/admin/credit-requests/{id}/resolve` |
| `configure-channel-runtime` | `POST /api/channels/{provider}/runtime-config` |
| `deconfigure-channel-runtime` | `DELETE /api/channels/{provider}/runtime-config` |
| `view-infra-pods` | `GET /api/v1/admin/infra/pods` |
| `view-infra-deployments` | `GET /api/v1/admin/infra/deployments` |
| `view-infra-events` | `GET /api/v1/admin/infra/events` |
| `view-infra-metrics` | `GET /api/v1/admin/infra/metrics` |
| `view-pod-logs` | `GET /api/v1/admin/infra/pods/{name}/logs` |

---

## Auth + for the audit live operator

Every audit row now also captures:

| Column | Type | Source |
|---|---|---|
| `actor_ip` | str(64) | `X-Real-IP` (after `AUTH_TRUSTED_PROXIES` trust chain) or TCP peer |
| `actor_user_agent` | str(512) | `User-Agent` header (truncated) |

For ops-console calls (the `X-Nova-Ops-Token` path), the actor is
`"ops-console"` and the IP/UA reflect the operator's browser, since the
Nova gateway terminates the call and the BFF forwards it server-side.

For pre-migration rows (created before this round of changes), `actor_ip`
and `actor_user_agent` are `NULL`. New rows backfill correctly.

---

## Half-wired endpoints (deferred to a future round)

- `POST /api/v1/auth/mfa/{enroll,verify,disable}` — TOTP MFA, not implemented.
- `POST /api/v1/auth/oauth/{provider}/callback` — OAuth login, declared config (`oauth_github_client_id/secret`) but no callback route.
- `POST /api/v1/auth/refresh` — refresh tokens, single access_token only.

These are explicitly out of scope for the current round and listed here
as the next obvious god-mode extensions.
