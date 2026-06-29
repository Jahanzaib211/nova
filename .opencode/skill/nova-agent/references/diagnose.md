# nova-agent: Diagnose Pipeline Regressions

Symptom → root cause → fix. Every bug listed here was confirmed in production.

---

## White preview / blank Browser tab

**Symptom**: Browser tab shows blank white page; self-test reports `✗ /index.html [console_errors] → 404 Not Found`.

**Root cause**: The Browser tab previews the dev server at `/` → `index.html`. When the deliverable is named anything other than `index.html` (e.g., `sovereign-demo.html`), the preview 404s.

**Fix (agent)**: Route the Browser tab to `/<filename>` using the route input. The agent-computer panel auto-detects non-index static HTML deliverables via the `canonicalHtmlArtifact` memo and sets the route automatically if the artifact basename ≠ `index.html`.

**Fix (prompt if agent misses it)**: Tell the agent: "The file is `outputs/my-file.html`. Open the Browser tab and set the route to `/my-file.html`."

**Not the cause**: Truncated HTML writes (verify with `wc -c` on the file), wrong palette (verify by opening the file locally), browser cache (hard-reload the panel).

---

## HTTP 409 "already has an active run"

**Symptom**: Red toast `HTTP 409: {"detail":"Thread ... already has an active run"}` when sending a message.

**Root cause**: User sent a message while the agent was still streaming. The gateway rejects concurrent runs by default (`multitask_strategy=reject`).

**Expected behavior**: The frontend catches 409 in `sendMessage`'s try/catch, rolls back the optimistic message, and shows a calm `t.common.agentBusy` toast. No red overlay.

**If the raw 409 HTTPError is surfacing** (red overlay): check `hooks.ts` `sendMessage` catch block — it should have `if (getHttpStatus(error) === 409) { toast.info(t.common.agentBusy); return; }`. If missing, the 409 handling was not applied.

**Fix**: Wait for the run to finish, or press Stop first. The Stop button now sends `cancel(action="interrupt")` to the gateway which halts the run server-side.

---

## Stop button not cancelling the run

**Symptom**: Clicking Stop shows the button changing but the agent keeps streaming.

**Root cause options**:
1. `handleStop` in `page.tsx` is not calling `stopRun()` — check it's `void stopRun()` not `pauseRun()`
2. `thread.stop()` aborts the client SSE stream but the backend run is still active — the explicit gateway cancel (`POST /runs/{id}/cancel?action=interrupt`) may not be firing
3. The `run_id` is not available when Stop is clicked (new thread, run not yet registered)

**Check**: In `page.tsx` → `handleStop` should call `stopRun()`. In `hooks.ts` → `stopRun` should call `thread.stop()` AND optionally hit the cancel endpoint.

**Verify backend**: `pm2 logs deerflow --lines 20 | grep "abort\|interrupt\|cancel"` — you should see "Run X abort requested — stopping" in the worker log.

---

## Resume continues from wrong state / starts fresh

**Symptom**: Clicking Resume reruns the task from the beginning instead of continuing.

**Root cause**: Backend worker was passing `{}` (empty dict) to `agent.astream()`. LangGraph only resumes from a checkpoint when invoked with `None` — `{}` is treated as fresh (empty) input.

**Fix** (already applied in `worker.py`):
```python
astream_input = graph_input if graph_input else None
```

If this regresses, verify `worker.py` has this line before both `agent.astream()` calls.

---

## Gateway crash-loop (186+ pm2 restarts)

**Symptom**: `pm2 list` shows `deerflow: online, restarts=186+, uptime=0s`. `docker ps` shows only `deer-flow-nginx` and `deer-flow-frontend`, no `deer-flow-gateway`.

**Root cause**: Docker name conflict. pm2 tries to `compose up` the full stack, hits "container name already in use" on `deer-flow-frontend`, exits before the gateway starts, pm2 immediately restarts — infinite loop.

**Fix**:
```bash
pm2 stop deerflow
docker compose \
  -f docker/docker-compose-dev.yaml \
  -f docker/docker-compose.dood.yaml \
  -p deer-flow-dev down --remove-orphans
pm2 start deerflow
until curl -sf http://localhost:2026/health; do sleep 3; done
echo "healthy"
```

**Verify**: `pm2 list | grep deerflow` → `online, restarts=0`. `docker ps | grep deer-flow` → all three Up.

---

## PrivacyPanel crash (Agent Computer panel)

**Symptom**: `TypeError: can't access property "size", status.cache is undefined` — the entire Agent Computer panel goes blank.

**Root cause**: The iGIN0 Privacy tab accesses `status.cache.size`, `status.cache.hit_rate`, `status.audit.total_records` without optional chaining. When the `/status` endpoint returns a partial payload (iGIN0 disabled or still loading), these crash the component.

**Fix** (already applied in `agent-computer-panel.tsx`):
```tsx
// Before (crashes):
value={`${status.cache.size}/${status.cache.max_size}`}

// After (safe):
value={`${status.cache?.size ?? 0}/${status.cache?.max_size ?? 0}`}
```

Apply the same `?.` guard to `status.audit?.total_records`, `status.audit?.errors`, `status.audit?.tor_usage`.

---

## Agent Computer panel stuck at top / content clipped

**Symptom**: The panel's content is pushed up; the footer is cut off; the terminal/browser tabs don't fill available height.

**Root cause**: The `RuntimeCapabilitiesBar` (h-9) stacks on top of `AgentComputerPanel` inside a non-flex wrapper. The panel tries to be `h-full` but the bar consumed height without the wrapper knowing, causing overflow.

**Fix** (already applied in `chat-box.tsx`):
```tsx
// The outer wrapper for the Agent's Computer panel:
<div className="flex h-full shrink-0 flex-col overflow-hidden">
  <RuntimeCapabilitiesBar className="shrink-0" />
  <div className="min-h-0 flex-1">
    <AgentComputerPanel ... />
  </div>
</div>
```

Key: `flex flex-col` on wrapper, `shrink-0` on bar, `min-h-0 flex-1` on panel container.

---

## Self-test reports issues but agent says "done"

**Symptom**: Activity tab shows `✗ Self-test found issues` but the agent's final message says the build is complete.

**Root cause**: The auto-verify-on-present_files fires in the background. If the run ended before the verify_result event was processed by the model, the agent never saw it.

**Fix**: The `ReflectFixBudgetMiddleware` is supposed to intercept the next model turn with the ISSUES directive. If it didn't fire, check:
1. That the `verify_result` custom event was emitted (check `onVerifyResult` in `page.tsx`)
2. That the reflect-fix budget is not already exhausted for that run
3. That the present_files auto-verify actually completed (check terminal for `[self-test]` lines)

**Workaround**: Tell the agent explicitly: "dev_verify found issues — look at the self-test results in the Activity tab and fix them."

---

## Diagnose checklist

When something's wrong and you don't know what:

```bash
# 1. Is the stack up?
curl -s localhost:2026/health  # 200?
pm2 list | grep deerflow       # online, restarts < 10?
docker ps | grep deer-flow     # all 3 containers Up?

# 2. Is the gateway logging errors?
docker logs deer-flow-gateway --tail 20

# 3. Is the frontend build broken?
cd frontend && pnpm lint && pnpm typecheck && pnpm build

# 4. Are backend tests passing?
cd backend && uv run pytest tests/test_harness_boundary.py tests/test_reflect_fix_middleware.py -q

# 5. Is there uncommitted broken code?
git diff --stat
git stash   # then retest, then git stash pop to restore
```
