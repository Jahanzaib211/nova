# SESSION HANDOFF — DeerFlow fork (read this FIRST)

> Written 2026-06-24. Owner is on a 3-day token cap (unreachable until ~2026-06-27).
> This is the single source of truth for resuming. If anything below disagrees with the
> code, **the code wins** — re-audit before acting.

---

## CURRENT STATE — updated 2026-06-28 (read this section first)

> Everything below the next `---` is the **v3** handoff (2026-06-24) and is now historical
> context. The fork has since advanced through v6 → v7 → v7.1 → iGIN0 v7.2. This section
> supersedes it for "what is the state today."

**Branch:** `fork-v7-browser-determinism` — 29 commits ahead of `origin/main`, tree clean.

**Build health (verified this session, all green):**
- `pnpm lint` → 0 errors (2 pre-existing exhaustive-deps **warnings** only:
  agent-computer-panel.tsx:214, input-box.tsx:589 — non-blocking).
- `pnpm typecheck` (`tsc --noEmit`) → clean.
- `pnpm build` (`next build`) → succeeds (79 routes).
- Frontend unit tests pass.

**Fixes landed 2026-06-28 (this session):**
1. **Agent's Computer panel "stuck at top" layout bug** — in
   `frontend/src/components/workspace/chats/chat-box.tsx` the right column stacked the
   additive `RuntimeCapabilitiesBar` (h-9) above `AgentComputerPanel` (h-full) inside a
   non-flex `h-full` wrapper, overflowing by the bar's height and clipping the panel
   footer. Wrapper is now `flex h-full flex-col overflow-hidden`; bar is `shrink-0`; panel
   sits in a `min-h-0 flex-1` child. Purely additive, reversible.
2. **Build-breaking lint error** — `tests/unit/core/reasoning-trigger.test.ts` passed
   `children` as a prop key (`react/no-children-prop`). Converted to JSX (`.test.tsx`),
   which satisfies both eslint and the typed-required `children` prop on `I18nProvider`.

**Production-grade audit of the 75-file diff vs main (+3572/−447):** clean. No real
TODO/FIXME/stub/placeholder, no leaked secrets in committed files (real keys live only in
gitignored `.env`/`config.yaml`), no stray `console.log` in `frontend/src`, no
`@ts-ignore`/`as any`/`eslint-disable` in changed frontend files. Backend broad
`except Exception:` blocks (capabilities.py, sandbox.py) are intentional best-effort
capability/health probes that degrade to a safe `unavailable`/`False`/`{}` status,
consistent with HARD RULE 5 (non-fatal run path).

**Open runtime item (not a code defect):** `config.yaml` is gitignored; its primary-model
choice (MiniMax `minimax-m3` vs `claude-sonnet` via the OAuth credential loader) is a
deploy-time decision, end-to-end LLM round-trip not re-verified this session.

**Out of scope / next:** Batch 4 refactors (split agent-computer-panel.tsx 1938 lines into
tab files, wrap blocking-IO sites, lazy-load landing). See plan
`~/.claude/plans/at-i-did-replaced-gleaming-octopus.md`.

---

## Originating directive (owner, 2026-06-24, verbatim)
> "now its time to built the deterministic — all the things that we can do to make the harness and
> computer use 100× better. I want you to audit the code as your source of truth, update at the end
> with an md file and a final reboot-proof check. Make it an enterprise killer that not even Manus and
> z.ai have — if anything in the harness can be improved, full stack, we do that. Place a session
> handoff document first because you are not gonna be available to me for 3 days of token cap."

This round delivered: handoff doc (this file), deterministic auto-verify-on-present_files gate,
Playwright persistence, FORK_V3.md, reboot-proof check. See `FORK_V3.md` and "OPEN / NEXT" below.

## Who / what
- **Goal:** turn DeerFlow into an enterprise-grade agentic build environment ("Manus / z.ai / Warp
  killer") with a **deterministic** computer-use layer (runtime-enforced, not prompt-hoped).
- **Roles:** owner = product owner; you (Claude) = lead full-stack dev + architect.
- **The fork changelog volumes:** `FORK.md` (v1), `FORK_V2.md` (v2), `FORK_V3.md` (v3 — this round).
- **Plan file:** `~/.claude/plans/you-are-restructuring-deerflow-luminous-crayon.md` (multi-round history).

## HARD RULES (never violate)
1. **Additive / reversible only.** No upstream rewrites. Every change must be revertible by one edit.
2. **Local-sandbox path byte-identical.** Branch on `is_local_sandbox(runtime)` / `sandbox_id` so the
   local provider behaves exactly as upstream. AIO is where the new computer-use lives.
3. **Batch backend edits.** uvicorn runs with `--reload`; **each reload recreates active sandboxes**, so
   apply backend changes when the live thread is idle, and group them.
4. **Anti-slop.** Reuse existing tools/components. No parallel re-implementations.
5. **Non-fatal.** Anything new in the run path (middleware, tools) must be wrapped so a failure can
   NEVER break a run. Owner can't fix a bricked stack for 3 days.
6. **Harness boundary.** `deerflow.*` never imports `app.*` (enforced by `tests/test_harness_boundary.py`).

## Stack / infra (how it runs)
- **PM2** process `deerflow` supervises the docker dev stack with the **dood overlay**
  (`docker-compose-dev.yaml` + `docker-compose.dood.yaml`, project `deer-flow-dev`). `ecosystem.config.js`
  matches this; `pm2 save` done; `pm2-jahanzaib.service` enabled in systemd → **reboot-proof**.
- Entry: nginx host port **2026**. Gateway is containerized (8001 inside; **host-exposed on 8000**,
  not 8001). Frontend 3000. Health: `curl localhost:2026/health` and `localhost:8000/health` → 200
  (port **8001 on the host returns 000 — that's normal**, it isn't published).
- `backend/.venv` is **host-mounted** → survives restarts/reboots, but **not a clean image rebuild**.
- Sandbox = **AIO** (`agent_sandbox` SDK, Docker per-thread). Under-used historically (only
  `shell.exec_command` + `file.*`); we tap more (PTY, browser-over-CDP, proxy).
- Health re-check command:
  ```
  curl -s -o /dev/null -w "%{http_code}" http://localhost:2026/health   # expect 200 (canonical)
  curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health   # expect 200 (direct)
  pm2 list | grep deerflow                                              # expect online
  systemctl is-enabled pm2-jahanzaib.service                            # expect enabled
  ```

## The deterministic computer-use layer (current state — what EXISTS)
All in `backend/packages/harness/deerflow/`:
- **Tools** (`tools/builtins/workspace_tools.py`, registered in `tools/tools.py` `BUILTIN_TOOLS`):
  `system_probe` (one-call env snapshot), `free_port` (fuser/lsof kill+verify),
  `dev_verify` (consolidated battery: tests→browser_check→code_review→PASS/ISSUES),
  `code_review`, `browser_check`, `save_skill`, plus enterprise nodes wrapping AIO SDK
  (`shell_session/view/wait/write/kill`, `browser_navigate/click/input/eval`, `deploy_expose`,
  `agent_notify`), and `scaffold_project`, `search_files`, `grep_files`, `start/stop_dev_server`.
- **Eyes:** `sandbox/browser_check.py::run_browser_check` uses **Playwright `connect_over_cdp`** to the
  existing AIO chromium (the `browser_page` HTTP API 404s on the current image — CDP is the working path).
  Renders running apps (goto) AND `present_files` static HTML (read via `cat` → `set_content`, no file://).
  Blank judged by **screenshot pixel bytes** (`_BLANK_SCREENSHOT_MAX_BYTES`), not innerText, so
  canvas/JS pages don't false-flag.
- **Deterministic runtime gates:**
  - Port hygiene before every preview start (`sandbox/dev_server.py::run_preview_pipeline` → `fuser -k`).
  - **Auto-verify-on-preview** (`_auto_verify_preview`): when a dev server goes ready, a background
    `browser_check` runs automatically — no model choice.
  - **Auto-verify-on-present_files** (THIS round, `observe_adjust_middleware.py`): when `present_files`
    completes, a background `browser_check` of the deliverable fires automatically. See FORK_V3.md.
  - Loop-detection budget **reset per run** + raised build-tool caps (`loop_detection_middleware.py`,
    `config.yaml` `tool_freq_overrides`).
- **Prompt** (`agents/lead_agent/prompt.py`): `<command_arsenal>` (Linux/computer mastery, literal paths —
  NEVER use `{...}` braces here, they break `.format()`), `<enterprise_capabilities>`,
  `<self_verify>` (mandates a single `dev_verify` call before declaring done).

## Gateway endpoints added (`backend/app/gateway/routers/sandbox.py`)
`/terminal-url`, `/absproxy/{thread}/{port}/{path}`, `/browser-check`, `/browser-check-last`,
`/review`, `/save-skill`, `/dev-start`. (ttyd terminal + VNC browser embeds + verify/review surfaces.)

## Frontend (Agent's Computer panel)
`frontend/src/components/workspace/agent-computer/agent-computer-panel.tsx`: tabs
**Files · Terminal · Editor · Browser · Activity · Review** (Audit folded into Activity). Terminal
embeds ttyd; Browser has VNC live-view; Editor has red/green live diff; auto-shows latest present_files
HTML. Hooks in `core/sandbox/hooks.ts`. Chat: ShineBorder on active turn, collapsed Reasoning, orphan
`</think>` stripped (`core/messages/utils.ts`).

## KNOWN CONSTRAINTS / GOTCHAS
- `browser_page.navigate` HTTP API **404s** on the current AIO image → always use Playwright-over-CDP.
- `file://` is invisible to the container browser → render HTML via `cat`+`set_content`.
- CLI test scripts must `uc.set_current_user(...)` with the **thread owner's user_id** or `read_file`
  mounts the wrong bucket.
- `_thread_id_from_sandbox_id` only resolves `local:` ids; for AIO resolve thread via `thread_data`.
- Playwright lives in the **host-mounted `.venv`** (survives restarts). It is now ALSO declared in
  `backend/packages/harness/pyproject.toml` so a clean rebuild keeps the agent's eyes.

## HOW TO RESUME (do this in order)
1. Read this file, then `FORK_V3.md`, then `git status` + `git diff --stat`.
2. Re-run the health re-check commands above. If gateway ≠ 200: `pm2 restart deerflow`, wait, recheck.
3. Pick up "OPEN / NEXT" below. Anything touching the run path: keep it non-fatal + test it.

## OPEN / NEXT (ranked)
1. **Live end-to-end verify of auto-verify-on-present_files** on a real authed build thread (could not
   drive an authed chat from CLI this session). Confirm the `verify_result` custom event renders in the
   Activity panel and `[self-test]` lines appear in the Terminal.
2. **Surface `verify_result` in the frontend Activity tab** if not already styled (event type is
   `verify_result`; mirror the `task_progress` handling).
3. **cloudflared quick-tunnel tier** for `deploy_expose` (tier-2 public URL, approval-gated) — designed,
   not built.
4. **Reflect/fix loop** (bounded): feed `dev_verify` ISSUES back as a structured event the lead consumes
   and re-runs until green or budget hit. Deterministic stop. (Capstone; behavior-changing — design with
   care, keep non-fatal.)

## Test / verify commands
```
cd backend && PYTHONPATH=. uv run pytest tests/test_loop_detection_middleware.py \
  tests/test_run_manager.py tests/test_harness_boundary.py -q
# browser_check live sanity (needs an active AIO thread + its owner user_id):
#   python scripts/<adhoc> using run_browser_check(thread_id, sandbox, ...)
```
