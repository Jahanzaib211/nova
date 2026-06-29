# nova-agent: Build Loop

The deterministic build loop Nova uses to construct and verify any full-stack app.
Every step is runtime-enforced — not prompt-hoped.

## The pipeline

```
scaffold_project / write_file
        ↓
bash (npm install / pip install)          ← use absolute paths; bash cwd = /home/gem NOT /mnt/...
        ↓
start_dev_server                          ← auto-verify fires when server goes ready
        ↓
dev_verify                               ← tests + browser_check + code_review → PASS / ISSUES
        ↓  ISSUES?
ReflectFixBudgetMiddleware injects        ← concrete failing items, forced fix turn
fix → dev_verify again (max budget=2)
        ↓  PASS
present_files                            ← delivers to user; auto-verify fires again
        ↓
BUILD_JOURNAL updated every tool cycle   ← agent reads it each turn for continuity
```

## dev_verify

The single deterministic gate before declaring done. Never skip it.

```python
# workspace_tools.py — what it runs:
# 1. Tests: pytest / npm test (if test files exist)
# 2. browser_check: Playwright CDP → render → screenshot → blank detection
# 3. code_review: REVIEW.md risk scan (rm -rf, git push, secrets, etc.)
# Verdict: ✅ PASS or ⚠️ ISSUES — <items>
```

Call: `dev_verify` (no args — it finds the running server and workspace automatically).

On ISSUES: `ReflectFixBudgetMiddleware` injects a forced `HumanMessage` with the exact failing items and forces another turn. The model fixes and calls `dev_verify` again. Budget = 2 consecutive ISSUES before hard-stop with honest failure message.

## bash cwd bug — critical

The `bash` tool's working directory is `/home/gem` by default, **not** `/mnt/user-data/workspace`. Inline `cd` in a command does not propagate. Two safe patterns:

```bash
# Pattern 1: absolute path flag
npm install --prefix /mnt/user-data/workspace/myapp

# Pattern 2: subshell
bash -c 'cd /mnt/user-data/workspace/myapp && npm install'
```

Never: `cd /mnt/user-data/workspace && npm install` (the cd doesn't stick between tool calls).

## start_dev_server

Starts the dev server and registers it with the preview proxy. The proxy routes `/` → the registered port.

```python
# dev_server.py — what it does:
# 1. Allocates a port (fuser -k kills conflicts first)
# 2. Detects project type (Next.js, Vite, CRA, Python)
# 3. Appends --host 0.0.0.0 so the container's published port is reachable
# 4. Streams stdout — marks "ready" on any of: "ready in", "ready on", "local:", "compiled"
# 5. Auto-verify fires once the server is ready (background browser_check)
```

If the server never marks "ready": look at the terminal stream for the actual error (missing deps, wrong cwd, port conflict). Use `free_port` if a port is stuck.

## Browser vision (supports_vision models)

When `dev_verify` runs with a vision-capable model, it captures a screenshot and feeds it to the model via `ViewImageMiddleware`. The model sees the actual rendered page — not just console errors.

Flow: `dev_verify` → `browser_check(with_screenshot=True)` → `verify_vision.stash_verify_screenshot()` → next model call → `ViewImageMiddleware._inject_verify_screenshot()` adds screenshot to messages.

The model can say "the layout is broken — the sidebar is overlapping the content" from the screenshot, not just from text output.

Gate: only fires when `config.supports_vision = true` for the active model. Non-fatal if skipped.

## BUILD_JOURNAL

Every tool cycle, `ObserveAdjustMiddleware` writes one generic event line derived from the tool name+args to `workspace/BUILD_JOURNAL.md` and the in-memory `_journals` buffer.

Named `BUILD_JOURNAL.md` (not `CHANGELOG.md`) so it never collides with a project's own changelog.

Each turn, the journal tail is injected as a `<build_journal>` system-reminder so the model always knows what it has built. Only injects when the journal changed (no prefix-cache churn).

The agent reads this each turn and uses it to:
- Not redo finished work
- Know which files have been written
- Track which dev_verify attempts succeeded

## Stop = pause, not kill

The Stop button sends `cancel(action="interrupt")` which preserves the LangGraph checkpoint. The run is paused, not terminated.

Frontend: `pauseRun()` calls `void thread.stop()` and sets `isPaused = true`.

Resume: `resumeRun()` calls `thread.submit(undefined, { threadId, ... })`. The frontend sends empty input; the worker maps `{}` → `None` (`astream_input = graph_input if graph_input else None`) so `agent.astream(None)` resumes from the checkpoint instead of starting fresh.

If the Resume button doesn't appear: check that `isPaused` is true and `thread.isLoading` is false. The banner renders only when `isPaused && !thread.isLoading`.

## present_files

Delivers final output to the user. Accepts only files under `/mnt/user-data/outputs`.

After a successful `present_files`, `ObserveAdjustMiddleware` fires a background `browser_check` of the delivered HTML (auto-verify-on-present_files). If the check finds issues, a `verify_result` event fires and the agent is given one more fix turn before the user sees "done".

Static HTML deliverables: the Browser tab previews `/` → `index.html`. If your deliverable is `my-app.html`, the preview will 404 on `/`. Either rename to `index.html` or set the route in the Browser tab's route input to `/<filename>`. The agent-computer panel auto-detects non-index static HTML deliverables (via `canonicalHtmlArtifact` memo) and routes the Browser tab correctly.

## Checklist before declaring done

- [ ] `dev_verify` returned `✅ PASS` (tests + browser + review all green)
- [ ] Screenshot shows the actual rendered page (not blank, not 404)
- [ ] `present_files` called with the output path
- [ ] BUILD_JOURNAL shows the key steps (written by middleware automatically)
