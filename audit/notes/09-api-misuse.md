# Phase 9 — Right API for the right job

Largely clean.
- **Timing-safe compares:** `secrets.compare_digest` used in CSRF (csrf_middleware.py:209), langgraph_auth.py:54, internal_auth.py:56, ops_auth.py:52. README CSRF "constant-time" claim ✅.
- **No shell-out where an SDK exists:** 0 `subprocess([...curl/git/wget...])` hits; HTTP via httpx, git via GitPython/porcelain where needed.
- **Async HTTP:** async paths use httpx.AsyncClient; `requests` confined to the sync `wait_for_sandbox_ready` (Phase 3).
- **"Append-only audit table":** in reality split — DB-backed admin audit + run_events (durable), in-memory hash-chained execution audit (SEC-014). The security-audit-trail-as-a-table framing is only half true.
- **Orchestration:** LangGraph owns the agent loop; the custom bits (RunManager/StreamBridge) are runtime plumbing, appropriate.

## Nit (P3, not filed separately)
`task_tool._await_subagent_terminal` / `_deferred_cleanup_subagent_task` poll the in-process subagent executor every 5s (task_tool.py:67,84). Since the executor is in-process, an `asyncio.Event`/Future would give lower-latency completion and drop the poll loop. Efficiency-only; correctness fine.

No new findings; cross-refs SEC-014.
