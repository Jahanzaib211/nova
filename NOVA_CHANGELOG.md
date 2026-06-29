# NOVA_CHANGELOG

> **Canonical forward-looking plan + change log for Nova** (formerly the DeerFlow
> fork; renamed at v4, rename tidy at v5). Continues `FORK.md`, `FORK_V2.md`,
> `FORK_V3.md`, `FORK_V4.md`, `FORK_V5.md`, `AUDIT.md`, `SESSION_HANDOFF.md`.
>
> **Audience:** the owner (Jahanzaib) + future contributors + the next agent
> that picks up after a token cap. Read this before touching anything.
>
> **What this is NOT:** a port of the upstream DeerFlow changelog
> (`CHANGELOG.md` / `CHANGELOG_zh.md`). Those still describe the base system.

---

## v7.4 — hooks surface + receipts + opencode skills (hackathon sprint)

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
