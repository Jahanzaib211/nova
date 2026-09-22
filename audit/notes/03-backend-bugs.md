# Phase 3 — Backend bug hunt

**Overall: the backend is unusually hardened.** The grep battery found the codebase already defends against most classic bugs:
- `shell=True`: **banned by construction** in the execution kernel (`execution/kernel.py:29`, `execution/models.py:155`, `policy.py:6`). Zero real `shell=True` calls.
- Blocking IO in async: a **Blockbuster gate** exists (`tests/blocking_io/`, 20 tests) and CI job. Every `time.sleep()` found (supervisor.py:534, tools.py:2140, recovery_service.py:115, claude_provider.py:311, browser_retry.py:238) is inside a **sync helper run in a thread** (`_worker`, `_fix`, `_schedule_async_termination`), not the event loop. `requests.*` appears only in the **sync** `wait_for_sandbox_ready` (aio_sandbox/backend.py:18); the async variant `wait_for_sandbox_ready_async` (:40) uses httpx.
- `os.system`/`eval`/`exec`: none in prod (redis `.eval()` is Lua server-side; `_exec` is a local helper name).
- Token generation via `random`: none (uses `secrets`/uuid).
- Weak hashes: `md5` only for non-security dedup (`loop_detection_middleware.py:161`) and **Tencent-protocol-required** signatures (`wecom.py:453`, `wechat.py:71`); `paths.py:59` sha1 with `usedforsecurity=False`. All acceptable.
- Subprocess timeouts: the `Popen` sites without a timeout are interactive PTY shells (`interactive_shell.py:129`, `kernel.py:140`) where a construction timeout is inapplicable.

## Real findings
- **BUG-001 (P3):** 9 naive `datetime.now()` sites; the risky ones are subagent lifecycle timestamps (`subagents/executor.py:133,509,826`). Rest are display/log/filename timestamps (benign).
- **BUG-002 (P3):** 90 `except …: pass` silent-swallow blocks — failure masking / observability gap.
- **Fire-and-forget tasks:** 27 `asyncio.create_task` without a handle. Sampled ones (`_auto_verify_preview` dev_server.py:488, `_watch_remote_cancel` manager.py:676) wrap their body in try. Discord typing/reaction tasks (discord.py:331-442) are unguarded but low-risk.
- **Module-level caches:** `_subagent_usage_cache` (task_tool.py:33) keyed by unique `tool_call_id`, set+pop — no cross-request race. `_tiktoken_encoding_cache`, `_enabled_skills_by_config_cache` are read-mostly memoization → Phase 8 (per-worker inconsistency, not correctness).
- **Resource note:** Phase 0 pytest emitted `PytestUnraisableExceptionWarning: Event loop is closed` from asyncio subprocess pipe teardown in `test_workspace_e2e.py` — a benign-looking teardown-ordering warning, but worth confirming no real subprocess-pipe leak under load (Phase 6/8).

26 TODO/FIXME/NotImplemented markers in prod → enumerated in Phase 10.
