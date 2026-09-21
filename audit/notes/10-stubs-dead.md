# Phase 10 — Stubs, dead code, missing features

**Very clean.** The scary-looking counts are almost all false positives:
- **98 "placeholder"** = 57 React input `placeholder=` attributes (legit UI) + ~30 backend uses that are avatar/placeholder-image/test-data strings, not functional stubs. No "return placeholder / not yet implemented" bodies found.
- **24 NotImplementedError** = abstract-method markers on ABCs (`auth/providers.py`, `auth/repositories/base.py`, `speech/base.py`, `sandbox.py:63` streaming, `pty_manager.py:87` POSIX-only, `json_compat.py:191` dialect guard). All intentional.
- **10 "TODO"** = mostly the agent's *Todo feature* identifiers (`_TODO_SYSTEM_PROMPT`, `TodoMiddleware`, `EMPTY_TODO_RESULT`). Only **one real code-TODO**: `frontend/src/components/workspace/input-box.tsx:736 {/* TODO: Add more connectors here */}` (cosmetic).
- **501 route:** `admin_infra.py:41` returns 501 by design when no provisioner is configured (documented optional), not a stub.
- **No dead UI controls:** 0 `onClick={() => {}}` / `onClick={undefined}`.
- **Skills:** all 27 `skills/public/*` have `SKILL.md` (Phase 1).

## Notable "was dead, now rebound"
`igino_research_tool` and `register_external_dev_server_tool` (tools.py:57-73) were dead for months (advertised in the prompt/manifest, never imported → "not a valid tool" errors) and are now bound but **degrade to an error string** when their backing service (SearXNG/TOR) is down. Self-documented. Worth a test that they load and that the degraded path is intentional.

No findings filed (all benign); one cosmetic TODO noted.
