# Nova vs. DeerFlow 2.0 — Verified Attribution

This document is the single source of truth for **what is stock DeerFlow 2.0** and
**what was built or changed for Nova**. Every number below was produced by diffing
this repository's tree against the upstream fork point — not from memory or
changelogs. You can reproduce every claim yourself (see [Verify it yourself](#verify-it-yourself)).

## TL;DR

| | |
| --- | --- |
| Fork base | [`bytedance/deer-flow`](https://github.com/bytedance/deer-flow) `v2.0.0-rc1` (`98127f58`, "Prepare 2.0.0 release") |
| Upstream files at fork point | 1,338 |
| Left untouched | 1,153 files (86%) |
| Modified for Nova | 180 files |
| **Built new for Nova** | **152 files (~28,700 lines)** |
| Total delta | 338 files changed, **+35,738 / −1,278 lines** |
| Backend delta | 107 files, +18,841 lines |
| Frontend delta | 116 files, +9,813 lines |
| New tests written | 37 new backend test files (7,803 lines); +8,112 test lines across backend/tests |

Nova is a full-stack refactor and extension of DeerFlow — backend and frontend —
not a UI skin. The sections below say exactly which is which.

## Verify it yourself

```bash
git remote add upstream https://github.com/bytedance/deer-flow.git
git fetch upstream --tags

# The full delta (matches the TL;DR table)
git diff --shortstat v2.0.0-rc1 HEAD

# Every file, classified: A = built for Nova, M = modified, D = deleted
git diff --name-status v2.0.0-rc1 HEAD

# Any single file's exact changes
git diff v2.0.0-rc1 HEAD -- <path>
```

Note: repo git history starts at a squashed baseline (much of Nova was built
locally before the first push), so tree-vs-tree diffing against the upstream tag —
as above — is the reliable comparison, and it is what this document is based on.

## What is stock DeerFlow 2.0 (upstream, credit where due)

These came with the fork and remain largely or entirely upstream code:

- **The super-agent harness itself** — lead agent + sub-agent orchestration,
  memory, LangGraph runtime, prompt framework.
- **Per-thread Docker sandboxes** — the AIO sandbox provider
  (`backend/packages/harness/deerflow/community/aio_sandbox/`). Nova modified 3
  files in it (preview-port plumbing, local backend fixes) but did not build it.
- **The skills system** — skill storage, loading, security scanning, and the
  bundled public skills (podcast/ppt/video/image generation, etc.).
- **Gateway (FastAPI) and auth base**, MCP server support, IM channels.
- **Frontend workspace base** — chat UI, thread management, agents pages, landing
  page, i18n framework, settings framework.
- **Deployment base** — Docker compose, nginx, setup wizard.

## What Nova modified (178 upstream files)

The significant ones, by subsystem:

| Area | Files (examples) | What changed |
| --- | --- | --- |
| SearXNG search client | `community/searxng/searxng_client.py`, `tools.py` | Rewrote the ~65-line basic client into a hardened one: retry with exponential backoff, circuit breaker, LRU+TTL cache, optional TOR routing, metrics |
| Loop detection | `agents/middlewares/loop_detection_middleware.py` | Added Layer-3 "dead-end search divergence" detector (catches N distinct failing searches for an invented name — invisible to the upstream hash-based detector) |
| Sandbox audit | `agents/middlewares/sandbox_audit_middleware.py`, `sandbox/tools.py` | Structured observation/record hooks feeding the Activity tab and `sandbox.log` audit trail |
| Lead agent prompt | `agents/lead_agent/prompt.py` | `<live_preview>` and `<self_verify>` protocol blocks, BUILD_JOURNAL.md self-knowledge |
| Models API | `app/gateway/routers/models.py` | Rewritten for runtime model add/remove with config reload (backs the Models settings page) |
| AIO sandbox | `aio_sandbox_provider.py`, `local_backend.py`, `sandbox_info.py` | Preview-port publication and local-backend fixes to support the live Browser tab |
| Frontend core | `core/threads/hooks.ts`, `chats/chat-box.tsx`, workspace pages | New custom event types (task_progress, verify_result, llm_error), Agent's Computer panel integration |
| i18n | `locales/en-US.ts`, `zh-CN.ts`, `types.ts` | ~350 new strings per locale for all Nova UI |
| Branding/docs | `README*.md`, landing components, icons | Nova rebrand |
| Skills | 19 files under `skills/public/` | Fixes and hardening of upstream skill scripts |
| Deployment | `docker/nginx/nginx.conf`, compose files | Proxy routes for new endpoints, ttyd/noVNC embeds |

## What Nova built (152 new files, ~28,700 lines)

None of the following existed in DeerFlow 2.0.

### 1. Verify loop — the agent tests its own builds (backend)

`backend/packages/harness/deerflow/sandbox/`

- `browser_check.py` — drives the sandbox's headless Chromium against the
  thread's running dev server: console errors, render failures, screenshot-pixel
  blank detection. Auto-triggered on dev-server-ready and on shipped HTML.
- `dev_server.py` — live dev-server registry (local + in-container modes) with
  auto-detection, port hygiene, and gateway HTTP proxying for live preview.
- `review.py` — deterministic (no-LLM) dual-audience code review: plain-English
  verdict for non-coders + per-file stats and risk flags for developers.
- Enterprise hardening around all of it: `browser_circuit_breaker.py`,
  `browser_retry.py`, `browser_errors.py` (typed exception hierarchy),
  `browser_check_concurrency.py` (bounded concurrency + total timeouts),
  `browser_tracing.py`, `metrics.py` (counters/histograms/gauges),
  `shutdown.py` (graceful teardown).

### 2. Agent self-correction middlewares (backend)

`backend/packages/harness/deerflow/agents/middlewares/`

- `observe_adjust_middleware.py` — emits live task-progress events + maintains
  `todo.md` in the sandbox after every tool cycle.
- `reflect_fix_middleware.py` — runtime-enforces the prompt's "iterate at most
  twice" rule so verify loops can't run away to the recursion limit.
- `preflight_quota_middleware.py` — checks provider quota *before* the LLM call;
  short-circuits cleanly instead of triggering retry storms.
- `strip_error_fallback_middleware.py` — stops synthetic LLM error messages from
  contaminating thread state on resume.
- `verify_vision.py` — hands the latest build screenshot to vision-capable
  models so the agent can *see* blank pages and broken layouts.
- `agents/manifest.py` — spawn-time agent self-knowledge primer with tool
  inventory auto-derived from the builtin registry.

### 3. 32 agent tools (backend)

`backend/packages/harness/deerflow/tools/builtins/workspace_tools.py` (1,075 lines)

- Shell sessions: `shell_session/view/wait/write/kill`
- Browser control: `browser_navigate/click/input/eval`, `screenshot`, `browser_check`
- Build loop: `scaffold_project`, `start/stop_dev_server`, `dev_verify`,
  `code_review`, `free_port`, `system_probe`, `deploy_expose`
- Workspace: `search_files`, `grep_files`, `save_skill`, `agent_notify`
- Plus `igino_research_tool.py` for the privacy research pipeline.

### 4. Gateway API surface (backend)

`backend/app/gateway/`

- `routers/sandbox.py` — SSE log stream, todo, and status endpoints powering the
  Agent's Computer panel.
- `routers/capabilities.py` — one call returning skills, tools, hooks, subagents,
  circuit-breaker states, and browser health.
- `routers/browser_health.py` — CDP reachability/latency probe endpoint.
- `routers/igino.py` — iGIN0 status/toggle/research/cache/audit endpoints.
- `auth_rate_limit_middleware.py` — sliding-window brute-force throttling on
  login/register/change-password.
- `config/runtime_models.py` — runtime model store kept separate from the
  hand-maintained `config.yaml`.

### 5. iGIN0 — privacy research stack (backend)

`backend/packages/harness/deerflow/community/searxng/`

- `tor.py` — TOR (SOCKS5) routing with logged direct-connection fallback.
- `search_cache.py` — thread-safe LRU+TTL result cache.
- `audit.py` — structured JSON privacy audit trail (query redaction supported).
- `search_errors.py` — typed error hierarchy.

### 6. Global skill promotion (backend)

`backend/packages/harness/deerflow/skills/promote.py` — security-scanned
promotion of a skill the agent built in its workspace into the global registry,
surviving container teardown and available in every future thread.

### 7. Agent's Computer (frontend)

- `agent-computer-panel.tsx` (2,668 lines) — the 6-tab live panel:
  **Terminal** (streamed bash), **Editor** (live code view with red/green diff),
  **Browser** (rendered preview + VNC), **Activity** (deduped action timeline +
  export), **Files** (repo tree + deliverables), **Review** (deterministic code
  review).
- `runtime-capabilities-bar.tsx` — status-first health/skills/metrics bar.
- `panels/auto-open-policy.ts` + context — panel opens only on real tool
  activity, respects user close, re-arms once per run.
- `lib/line-diff.ts` — LCS line-diff engine for the Editor's live-diff view.
- `core/sandbox/hooks.ts`, `core/runtime/`, `core/igino/` — typed API layers.
- `settings/models-settings-page.tsx` — runtime model management UI.
- `error-boundary.tsx`, `agent-computer-error-boundary.tsx`, `global-error.tsx`.
- `scripts/live-smoke.ts`, `live-smoke-deep.ts` — live smoke-test harnesses.

### 8. Ops & reliability layer

- `scripts/healthcheck-daemon.py` — 11-probe supervisor watchdog with
  registered auto-fixes (containers, llama-bridge drift, litellm), per-probe
  exception isolation, and a self-deadline.
- `ecosystem.config.js` + `scripts/pm2-deerflow.sh` + `scripts/pm2-litellm.sh` —
  PM2-owned lifecycles for the Docker stack, the llama-bridge, and the
  LiteLLM proxy.
- `docker/litellm/config.yaml` — LiteLLM model gateway config: Nova reaches
  free Ollama cloud models (MiniMax M3, Nemotron 3 Super, Qwen3 Coder 480B,
  GPT-OSS 120B) through one OpenAI-compatible proxy bound to the docker
  bridge IP.
- `scripts/setup-reboot-persistence.sh` — systemd ordering fix so PM2 waits for
  Docker (eliminated a 923k-restart crash loop).
- `docker/nginx/nginx.tls.conf`, `docker/searxng/settings.yml`.

### 9. Agent tooling & skills config

- `.opencode/` overlay — `nova-agent` master skill (build-loop / maintain /
  diagnose) + 8 nova skills + 5 command wrappers.
- `extensions_config.json` — skills/MCP registration.
- 3 new public skills: `code-reviewer`, `qa-tester`, `file-organizer`.

### 10. Tests

38 new backend test files (9,000+ new test lines repo-wide) covering
browser_check, retry, circuit breaker, shutdown, dev-server preview, verify
events, middlewares (including system-message coalescing for strict local
chat templates), manifest, capabilities endpoint, healthcheck daemon,
serialization stripping, deterministic-review risk scanners, and more.

## Historical documents

`FORK.md` through `FORK_V5.md` and `NOVA_CHANGELOG.md` are point-in-time
development snapshots kept for history. Where they disagree with this document,
this document (and the diff commands above) win.
