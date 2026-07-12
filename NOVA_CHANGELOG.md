# NOVA_CHANGELOG

> **Canonical forward-looking plan + change log for Nova** (formerly the DeerFlow
> fork; renamed at v4, rename tidy at v5).
>
> **Audience:** the owner (Jahanzaib) + future contributors + the next agent
> that picks up after a token cap. Read this before touching anything.
>
> **What this is NOT:** a port of the upstream DeerFlow changelog
> (`CHANGELOG.md` / `CHANGELOG_zh.md`). Those still describe the base system.

---

## v8.0 — Phase C0: foundation + streaming hardening

**Session pattern:** forensic production investigation → targeted hardening → consolidation tracking.

### Production incident (2026-07-12)

- **HTTP 500 regression:** Phase C0 added `correlation_id` to `RunRow` ORM model but created no Alembic migration. SQLAlchemy's `Base.metadata.create_all()` only creates tables, not columns — live SQLite DB had 24 columns while ORM expected 25. Every new run creation failed with `OperationalError: no such column: runs.correlation_id`.
- **Fix:** `ALTER TABLE runs ADD COLUMN correlation_id VARCHAR(64)` inside running gateway container + new idempotent Alembic migration `2026_07_12_phase_c0_correlation_id.py` + `env.py` updated with `DEER_FLOW_DATABASE_URL` env-var override.

### Streaming hardening (Phases 1–6)

- **Phase 1:** Watchdog with 12 probes (P1–P12) covering nginx, gateway, frontend, local LLM stack, containers, binary attestation, and Cloudflare tunnel.
- **Phase 2:** Bounded Stop + Force Disconnect — stop is a state machine with configurable timeout.
- **Phase 3:** Active-run polling — never fully disabled while a run exists.
- **Phase 4:** Convergent teardown — deterministic cleanup on disconnect.
- **Phase 5:** Backend store-only cancel — cancel persists through RunStore, startup reaper cleans stale runs.
- **Phase 6:** Tunnel auto-recovery — `fix_tunnel()` calls `systemctl reset-failed` before restart, verifies `is-active` for up to 10s post-restart. PM2 lock path moved to `$XDG_RUNTIME_DIR`.

### Cross-process correlation (Phase C0)

- **Backend:** `RunRecord.correlation_id` field, SSE comment emission (`: correlation_id=<hex>`), `RunManager.create()` generates UUID hex.
- **Frontend:** `stream-liveness.ts` captures correlation_id from SSE comments, `stream-trace.ts` propagates to `DiagnosticsRecord`.
- **Diagnostics:** `register_correlation_id()` / `lookup_correlation_id()` thread-scope helpers, module-level registry.
- **Tests:** 29 backend streaming/correlation tests, 13 frontend correlation capture + round-trip tests.

### CI guardrails

- `scripts/check_platform_guardrails.py` — middleware sprawl check (baseline: 28, allowed: 29) + duplicate-recovery detector (canonical site: `hooks.ts`).
- Guardrails run on every commit via AGENTS.md self-test protocol.

### Consolidation tracking

- `CONSOLIDATION.md` established as single source of truth for platform consolidation program.
- Directives 1–10 tracked with phases C0–C5.

### Tests

- 457 frontend unit tests (49 files).
- 29 backend streaming/correlation tests.
- 5 tunnel recovery tests.
- Cross-ref check passes (1497 files).

---

## v7.5 — live audit hardening + Ollama/LiteLLM free-model gateway

**Session pattern:** full-stack live audit (act-as-user via Playwright) → every blocker turned into a production-grade fix with a regression test.

### Audit fixes (all with tests)

- **Preview proxy 500 (P0):** the Batch-3.1 per-method route wrappers in `app/gateway/routers/sandbox.py` were sync `def` returning un-awaited coroutines — every `/api/sandbox/preview|lpreview|absproxy` request 500'd ("'coroutine' object is not iterable"), breaking the Browser tab. Made async; pinned by `test_preview_route_handlers_async.py`.
- **llama-bridge drift:** pm2's saved dump pointed at a deleted script path; the "online" process was orphaned stale code on the wrong port. Re-registered from `ecosystem.config.js`; watchdog `fix_llama_bridge()` now heals both crash and drift.
- **Watchdog gaps:** P9 had no auto-fix and warned every 30s unread for days. Added P9 + P10 auto-fixes and streak-deduped the no-auto-fix warning.
- **Strict chat-template 400:** middlewares inject SystemMessages mid-conversation; llama.cpp templates reject system at position > 0. New `SystemMessageCoalescingMiddleware` (innermost) folds them into the single leading system message.
- **Context overflow classified:** llama.cpp `exceed_context_size_error` now maps to a non-retriable `context_overflow` reason with an actionable message.
- **Silent verify skip:** auto-verify-on-present_files no-opped silently on non-AIO sandboxes — a corrupted deliverable shipped as "verified clean". Skip is now logged as "deliverable NOT verified".
- **Clobbered-HTML backstop:** deterministic review flags `.html` deliverables that don't start like HTML (`_scan_malformed_html_risks`) — catches the chunked-write overwrite failure observed live (maree.html written 3×, each write clobbering the last).
- **Orphan tool messages:** frontend grouping now renders tool results whose parent AI message was stripped instead of console.error + drop.
- **CSP blocked the Browser tab (nginx):** the app-shell CSP had no `frame-src`, so the blob-URL preview iframe (which inherits the parent page's CSP) was refused, and Google Fonts pulled by generated HTML were blocked. `docker/nginx/nginx.conf` `$csp_policy` map now allows `frame-src 'self' blob: http://localhost:*` plus fonts.googleapis.com/fonts.gstatic.com in the app-shell policy only — the strict `/api/` policy (`default-src 'none'`) is untouched. Note: the nginx container copies the mounted conf from a template at start, so `docker restart deer-flow-nginx` (not `nginx -s reload`) is required to apply edits.

### Ollama + LiteLLM free-model gateway

- `docker/litellm/config.yaml` + `scripts/pm2-litellm.sh` + pm2 app `nova-litellm` (LiteLLM 1.91.0 in a dedicated venv at `~/.nova-litellm`, bound to the docker bridge IP only).
- Four verified-free Ollama cloud models registered via the runtime models API: MiniMax M3, Nemotron 3 Super, Qwen3 Coder 480B, GPT-OSS 120B (`*-free`), reachable from the gateway at `host.docker.internal:4000/v1`.
- Watchdog P10_litellm probe + auto-fix; 10/10 probes green.
- Settings → Models: new "Add Ollama model (via LiteLLM)" preset (en/zh locales).

### Ecosystem: OpenCode + Dify on the same LiteLLM gateway

- **OpenCode**: `nova-litellm` provider in the global config (`~/.config/opencode/opencode.jsonc`) with all four free models and real limits pulled from `ollama show` (MiniMax M3 524K / Nemotron 262K / Qwen3-Coder 262K / GPT-OSS 131K context, 64K output); nova's `.opencode/opencode.json` pins `qwen3-coder-480b-free` as project default. Verified live via `opencode run`.
- **Dify** (fork `Jahanzaib211/dify` at `~/Desktop/dify`): full stack under PM2 as `nova-dify` (foreground compose, same pattern as `deerflow`), UI on `127.0.0.1:8088` only — upstream's 0.0.0.0 plugin-debug mapping replaced via `ports: !override`. Containers reach LiteLLM through `host.docker.internal:host-gateway`. Provider + 4 models configured as a real user via Playwright (context sizes set explicitly — the OpenAI-compatible plugin defaults to 4096); E2E chat verified ("DIFY-LITELLM-OK" on minimax-m3-free). Session lifetimes raised for the localhost-only install (access 7d / refresh 365d). Fork-side files committed to `Jahanzaib211/dify@main`.
- **Watchdog P11_dify**: probes `/console/api/setup` for `step=finished` (api-up-but-uninitialized reads as unhealthy); auto-fix heals the `nova-dify` pm2 app with re-register-on-drift. 11/11 probes green live; regression tests added (probe validator, both fix paths, dispatch streak).

### Attribution

- `NOVA_VS_DEERFLOW.md` — verified upstream-vs-Nova attribution map (fork base deer-flow v2.0.0-rc1, reproducible diff commands); README "What Nova adds" rewritten to match.
- Attribution numbers recomputed pre-commit: 338 files, +35,738/−1,278 vs v2.0.0-rc1 (152 new files ~28.7k lines; 37 new backend test files).
- README hero: `docs/images/nova-workspace.png` — real capture of a MiniMax M3 (free) session building a tip calculator, previewed live in the Agent's Computer Browser tab (zero console errors at capture).

---

**Sprint window:** 5 days. **Pattern:** Sequential Pipeline + De-Sloppify + per-day CI gate.
**Loop pattern (per Autonomous Loops skill):** daily `claude -p` chain, context bridge via `SHARED_TASK_NOTES.md`, magic-phrase completion signals.

### Opencode ecosystem integration (Matt Pocock fork)

The user's fork at `Jahanzaib211/skills` (forked from `mattpocock/skills`) provides workflow + discipline skills. Three integration layers:

- **Layer 1 (zero code change):** opencode commands wrapping Matt Pocock's user-invoked skills (`/grill-me`, `/to-prd`, `/triage`, `/improve-codebase-architecture`, `/setup-matt-pocock-skills`)
- **Layer 2 (one-line code change):** `extensions_config.json` registry update for Matt Pocock's model-invoked discipline skills (`tdd`, `diagnosing-bugs`, `codebase-design`, `domain-modeling`); modify `skill_storage.py:_iter_skill_files` to walk `~/.claude/skills/mattpocock/`
- **Layer 3 (no code change):** orchestration loop — user runs `/grill-with-docs` → skill outputs PRD → agent executes with TDD discipline → receipts + watchdogs + self-improvement closes the loop

### Day-by-day plan

| Day | Task | Magic phrase | Layer additions |
|---|---|---|---|
| D1 | opencode overlay + 8 nova skills | `NOVA_V74_D1_COMPLETE` | Layer 1 (5 opencode commands) + Layer 2 (registry) |
| D2 | hooks surface (resurrect `record_middleware`, wire 5 middlewares) | `NOVA_V74_D2_COMPLETE` | Layer 2 (skill_storage.py path) |
| D3 | receipts + watchdogs + cronjobs | `NOVA_V74_D3_COMPLETE` | — |
| D4 | nova CLI + Receipts/Improvements tabs + Self-improving loop | `NOVA_V74_D4_COMPLETE` | — |
| D5 | coverage + lint cleanup + release | `NOVA_V74_SPRINT_COMPLETE` | — |

### Opencode custom skills (8) created in D1

Each skill references real file paths in this repo (no hallucination):

- `nova-replay-verify` — prove the thesis via replay-golden
- `nova-hooks-author` — the keystone primitive (`runtime/journal.py:529`)
- `nova-receipts-author` — structured post-run artifacts
- `nova-subagent-router` — 40-60% token savings
- `nova-loop-detect` — debug stuck agents (`loop_detection_middleware.py:837`)
- `nova-sandbox-debug` — local vs AIO triage
- `nova-deploy-local` — canonical `make dev` path
- `nova-prompt-author` — lead-agent system prompt discipline

### Sprint verification matrix (per day, exit gate)

- `cd backend && uv run pytest tests/test_replay_golden.py tests/test_capabilities_endpoint.py tests/test_igino_endpoint.py tests/test_openapi_operation_ids.py -q` — keystone
- `cd backend && uv run ruff check` — clean
- `cd frontend && pnpm format && pnpm lint && pnpm typecheck` — clean
- Manual 2-minute Loom path works (per the demo flow)

### Anti-slop invariants (from the AI-First Engineering skill)

- Every layer reuses existing primitives (`record_middleware`, `BUILTIN_TOOLS`, `RunEventStore`, `subagents/executor.py`, `scripts/serve.sh`, `sandbox/browser_circuit_breaker.py`)
- No parallel implementations
- `deerflow.*` never imports `app.*` (enforced by `tests/test_harness_boundary.py`)
- Operational IDs preserved: `deer-flow` folder, `deer-flow-dev` compose project, `deer-flow-*` containers, `deerflow.*` Python package, `DEER_FLOW_*` env vars, `backend/.deer-flow/` data dir
- Non-fatal: every new code path is wrapped so a failure can never break a run

### Rollback

`git tag pre-hackathon-sprint-rollback-20260629` (pre-D0) is the instant-revert anchor. Per-day revert: `git revert <D[n-1]-sha>..<D[n]-sha> -- <paths>` for the specific files touched in that day.

---

## Operational IDs (intentionally preserved)

- docker-compose project name: `deer-flow-dev`
- Container names: `deer-flow-{nginx,frontend,gateway}`
- pm2 process: `deerflow`
- Python package: `deerflow.*`
- Env vars: `DEER_FLOW_*`
- Data dir: `backend/.deer-flow/`

These are stable; only docs / user-facing strings change.

---

## Hard rules (carried forward from FORK_V* + AI-First Engineering)

1. **Additive / reversible only.** Every change is one revert away.
2. **Local-sandbox path byte-identical.** `is_local_sandbox` branch (sandbox/tools.py:1204) preserved.
3. **Batch backend edits.** uvicorn `--reload` recreates active sandboxes.
4. **Anti-slop.** Reuse existing primitives; no parallel implementations.
5. **Non-fatal.** Every new code path is wrapped so a failure can never break a run.
6. **Harness boundary.** `deerflow.*` never imports `app.*` (enforced).
7. **Replay E2E is the contract.** `tests/fixtures/replay/write_read_file.ultra.{json,events.json}` is the keystone test.
