---
name: nova-sandbox-debug
description: Debug a Nova sandbox failure (Local vs AIO) — which provider is active, why `browser_check` / `dev_verify` / `save_skill` are skipping, and how to flip providers. Use when the user reports "browser_check failed: requires the container sandbox", "save_skill wrote to wrong path", or "dev_server can't bind port 4100". Routes through `is_local_sandbox` (`sandbox/tools.py:1204-1221`), the LocalSandboxProvider vs AioSandboxProvider switch (`sandbox_provider.py:60-74`), and the AIO shell-session recovery path (`community/aio_sandbox/aio_sandbox.py:131-148`). Triggers on phrases like "sandbox won't start", "browser tool doesn't work", "save_skill went to host not container", or after observing `reason: "this sandbox has no browser"` in a `BrowserCheck` response.
---

# nova-sandbox-debug

## Triage

1. Which provider? `python -c "from deerflow.sandbox import get_sandbox_provider; print(type(get_sandbox_provider()).__name__)"`. Returns `LocalSandboxProvider` or `AioSandboxProvider`.

2. Read `config.yaml:sandbox.use` — controls which provider class is loaded via reflection (`sandbox_provider.py:60-74`).

## Common failure patterns

| Symptom | Cause | Fix |
|---|---|---|
| `browser_check: "this sandbox has no browser (container/AIO sandbox required)"` | Running `browser_check` on Local sandbox (line 334-336 in `sandbox/tools.py`) | Switch to AIO in `config.yaml` OR disable the browser in the test |
| `save_skill: /mnt/user-data/skills/...` — file lands in wrong dir | `save_skill` host_root fallback ran instead of AIO sandbox (workspace_tools.py:979) | Confirm `is_local_sandbox()` returned False; check `start_dev_server` ran on AIO |
| `dev_server: port 4100 already in use` | Zombie AIO process holding the port (dev_server.py:161-169 port hygiene) | `free_port_tool` or `fuser -k 4100/tcp` |
| `shell_session: AIO shell session corrupt` | Race condition in concurrent `exec_command` (aio_sandbox.py:131-148) | Recovery path: the code creates `fresh_id` and retries automatically. If still failing, the container needs restart. |
| `_ERROR_OBSERVATION_SIGNATURE` in shell output | AIO shell session is corrupt; recovery branch triggers (aio_sandbox.py:134) | If retries exhaust, restart the AIO container |

## Anti-patterns

- ❌ **Don't** add a new `is_local_sandbox` branch — there are already 21+ call sites. If you need different behavior, add it to the provider's method, not the branch.
- ❌ **Don't** switch to Local to "make it work faster" — Local can't run browser_check, dev_verify, save_skill (host-root), or PTY tools.
- ❌ **Don't** hardcode a port — use `allocate_container_port(thread_id, label)` in `dev_server.py`.
