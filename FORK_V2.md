# DeerFlow — Fork Changes, Volume 2 (Enterprise Agent's Computer)

This is the **volume 2** companion to [`FORK.md`](FORK.md). Volume 1 introduced the Agent's Computer
3-panel UI, one isolated Docker container per conversation, and the live streaming preview. This
volume covers everything built **on top of that** to make it an enterprise-grade, self-verifying build
environment — a "Manus / z.ai / Warp killer" on the DeerFlow stack.

Everything here is **additive and reversible**: the upstream local-sandbox path stays byte-identical
(branch on `is_local_sandbox`), and every enterprise node wraps the **native AIO sandbox SDK**
(`agent_sandbox`) that stock DeerFlow under-uses (it only called `shell.exec_command` + `file.*`).

---

## 1. Reliability — builds run to completion (no FORCED STOP)

Big builds used to die at `[FORCED STOP] Tool bash called 50 times`. Root cause: the loop-detection
frequency counter was **per-thread-lifetime**, never reset per run.

- `agents/middlewares/loop_detection_middleware.py` — reset `_tool_freq` per run in `before_agent`
  (the cap is now per-run, the correct scope for a loop).
- `config.yaml` `loop_detection.tool_freq_overrides` — `bash/write_file/str_replace = warn 120 / hard 250`
  per run; other tools keep 30/50 (real loops still caught).

## 2. Deterministic preview + self-test (no LLM in the loop)

- `sandbox/dev_server.py::run_preview_pipeline` — find project → `npm install` (if needed) → start dev
  server on a published port. Triggered by `POST /api/sandbox/dev-start` + an auto-trigger; preview
  persists via **TCP liveness** (`dev_status`), not the log-poller.
- **Auto verify-on-preview**: when a preview comes up, a browser self-test runs automatically and logs
  `[self-test]` lines — the "always testing, getting better" loop.
- **Robust any-port preview** — `GET /api/sandbox/absproxy/{thread}/{port}/{path}` proxies through the
  AIO sandbox's own `/absproxy/{port}/` gateway, so an app on **any** in-container port (5173, 3000…)
  previews correctly regardless of how the agent started it.

## 3. Browser self-test (native Chromium, zero install)

The AIO sandbox ships a real Chromium (`browser_page` SDK + CDP + VNC). DeerFlow now uses it:

- `sandbox/browser_check.py` + `browser_check` tool + `POST /api/sandbox/browser-check` — loads the
  running app, captures **console errors + render-failure detection + a screenshot**, detects the real
  listening port (`ss`/netstat) so manually-started servers self-test correctly.
- Frontend: 👁 **Self-test** button in the Browser tab (pass/fail + console + screenshot), auto-shown
  via `/browser-check-last`.

## 4. Self-improving loop (enterprise-grade, bounded)

- `agents/lead_agent/prompt.py` `<self_verify>` — after a build the agent runs `browser_check` +
  `code_review` and fixes issues before declaring done, bounded to ≤2 iterations (no token sink).
- Reliable subagent fan-out kept (the loved `ShineBorder` subtask effect; subagents enabled in pro+ultra).

## 5. Dual-audience code review

- `sandbox/review.py` + `code_review` tool + `GET /api/sandbox/review` — deterministic review from
  git/file-scan + the audit trail: a **plain-English verdict** (non-coders) AND a **developer section**
  (per-file +/- stats, risk flags, detected checks). Writes `REVIEW.md`.
- Risk heuristic hardened (e.g. `vercel deploy` not bare `vercel`, which had matched `vercel-labs/skills`).
- Frontend: **Review tab** (regenerate + download).

## 6. Global plugin skill system (skills survive teardown)

- `skills/promote.py` + `save_skill` tool + `POST /api/sandbox/save-skill` — promote a skill the agent
  built/installed into the **global** `skills/custom/<name>` registry (security-scanned via the existing
  scanner). Custom skills default to *enabled*, so a saved skill appears immediately in the ✨ launcher
  and as `/<name>` in **every future thread**. Prefer this over ephemeral `npx skills add`.
- **Per-agent skill toggles** — the agent card has a ✨ Skills dialog writing `AgentConfig.skills`
  (None = inherit all / explicit list = deterministic node-level whitelist / [] = none).

## 7. Enterprise capability nodes (wrap the native AIO SDK)

New agent tools in `tools/builtins/workspace_tools.py` (registered in `tools/tools.py`):

| Tool | Wraps | Purpose |
|---|---|---|
| `shell_session` / `shell_view` / `shell_wait` / `shell_write` / `shell_kill` | `client.shell.*` | Persistent **interactive PTY** — REPLs, watchers, prompts (vs one-shot `bash`) |
| `browser_navigate` / `browser_click` / `browser_input` / `browser_eval` | `client.browser_page.*` | Real **browser automation** |
| `deploy_expose(port)` | absproxy route | A shareable preview URL for any running port |
| `agent_notify(message)` | audit log | Post progress milestones to the Activity feed |

## 8. Live embeds (watch/drive the sandbox directly)

- `GET /api/sandbox/terminal-url` — returns direct host URLs for the sandbox's **ttyd terminal** and
  **noVNC browser**, derived from the container's published 8080 port (browser shares the Docker host,
  so it reaches `localhost:{port}` directly — no fragile WS-proxy).
- Frontend: Terminal tab **Stream ↔ Shell** toggle (Shell = a real interactive terminal you can type
  in); Browser tab **VNC** button (watch the agent's live browser).

## 9. UI / IA unification (enterprise, no duplication)

- **Tabs redesigned** (`agent-computer/agent-computer-panel.tsx`): **Files** (real repo tree + Outputs)
  is now the first tab; **Activity** is a deduped action timeline + JSONL export (the old **Audit** tab
  was a duplicate and was removed); **Terminal/Editor/Browser/Review** each distinct.
- **Header decluttered** — primary actions (✨ Run skill · Close) + a `⋯` overflow menu for
  GitHub/zip/download (no icon soup).
- **Cowork removed** — it duplicated Settings → Skills; deleted nav + route + component. New Chat is the
  single launcher (the `?skill=`/`?prompt=` prefill remains for links).
- **Chat UX**: reasoning is **collapsed by default** with a shimmer "Thinking…" trigger (no auto-opening
  big box); the raw bash **command/output is no longer dumped in chat** (it lives in the Terminal); the
  loved subagent **`ShineBorder`** glow now also wraps the active assistant turn; orphan `</think>`
  leak fixed (`core/messages/utils.ts`).

## 10. Reboot-proofing

`ecosystem.config.js` runs the dev stack under PM2 with the **dood overlay** (so AioSandboxProvider
keeps the host Docker socket across reboots). Re-register with:
```
pm2 delete deerflow && pm2 start ecosystem.config.js --only deerflow && pm2 save
```
`pm2 save` persists it so a reboot resurrects the dood-enabled command and per-thread sandboxes work.

---

## New endpoints (all under `/api/sandbox`, in `app/gateway/routers/sandbox.py`)
`dev-start` · `dev-status` · `dev-servers` · `review` · `browser-check` · `browser-check-last` ·
`save-skill` · `absproxy/{thread}/{port}/{path}` · `terminal-url` · `audit` (+JSONL export).

## New harness modules (`backend/packages/harness/deerflow/`)
`sandbox/review.py` · `sandbox/browser_check.py` · `skills/promote.py` ·
`agents/middlewares/observe_adjust_middleware.py` (task-progress + auto todo.md) · the enterprise tools
in `tools/builtins/workspace_tools.py`.

## Verification status
Frontend `tsc --noEmit` + ESLint clean; backend `test_harness_boundary` + `test_dev_server_preview` +
`test_loop_detection_middleware` + `test_lead_agent_skills` green; 32 agent tools load with no
duplicates; gateway healthy; preview/review/self-test verified end-to-end on the live PM2/AIO stack.
