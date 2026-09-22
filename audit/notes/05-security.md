# Phase 5 — Security of the security

## What is solid
- **Middleware order** (Phase 2): CORS→RateLimit→CSRF→Auth→routers. Correct.
- **CORS**: `*` explicitly stripped (`csrf_middleware.py:111` `if origin == "*": continue`) → no wildcard-with-credentials. ✅
- **JWT**: `algorithms=["HS256"]` pinned (`auth/jwt.py:48`) → no alg:none / HS-RS confusion. ✅ (gap: no aud/iss/require-exp → SEC-015)
- **CSRF**: double-submit token; login/register exempt but origin-checked (`is_allowed_auth_origin`), `*` and hostile Origin rejected; Origin-less (curl/mobile) allowed by design.
- **Auth/Guardrail fail-closed**: present and default-on (`guardrails/middleware.py:68-92`, `app.py:399`).
- **BYOK**: Fernet from `NOVA_BYOK_SECRET` env (`byok.py:31-45`), disabled if key invalid/unset, ciphertext never returned plaintext. Key is env-provided (not derived, not co-located with ciphertext). ✅
- **Admin audit** persisted to DB (`persistence/admin_audit/AdminAuditRow`); run_events DB-backed.
- **Execution kernel**: `shell=True` banned by construction; argv-only; hash-chained audit.

## The serious problems (ranked)
1. **SEC-011 (P0):** live sandbox = `nova-sandbox-android:latest` + `privileged: true` (`config.yaml:239,272`, comment "Effectively host root"). Example config ships `privileged: false`. Subagents share the sandbox. This box also hosts Postgres, all tunnels, ~30 PM2 apps → a sandbox escape is a full-host compromise.
2. **SEC-010 (P1):** the command classifier is a **regex denylist** with trivial bypasses that are NOT in its self-congratulatory corpus: `head/less/xxd /etc/shadow` (only `cat` matched), `rm -rf /etc`, `rm -rf / --no-preserve-root`, two-step `curl -o /tmp/x && bash /tmp/x`, `nc -e /bin/sh`, `python3 -c` reverse shells, `base64 -d p > f; sh f`. It's advisory, not a control — but SEC-011 makes it the last line before host root.
3. **SEC-013 (P1):** IM channels default **allow-all** (empty `allowed_users`); anyone can drive a privileged agent and spend tokens. No per-user budget.
4. **SEC-014 (P2):** execution/security audit trail is in-memory `deque(maxlen=10000)` — not durable, silently evicted, hash chain broken by eviction.
5. **SEC-012 (P2):** running image is floating `:latest`, not digest-pinned (contradicts README).
6. **SEC-002 (P2, Phase 0):** live `.env` duplicated into 2 stray `.kilo/worktrees/*/.env`.
7. **SEC-015 (P3):** JWT no aud/iss/require-exp.

## Not executed
All classifier bypasses are paper analysis of the regexes in `sandbox_audit_middleware.py:25-60`. No commands were run in any sandbox (per rules of engagement).

## Red-team tool exposure (ties to CLAIM-001)
The offensive tools that ARE in the image (nmap, sqlmap, nuclei, hydra, etc.) have no host-target restriction enforced in code that I found — "Metasploit host-only" is a doc statement, and most heavy tools aren't installed anyway. With SEC-013 (open bot) + SEC-011 (privileged), an external user could run the installed scanners against arbitrary targets from this host's IP. → confirm in Phase 7/roadmap.
