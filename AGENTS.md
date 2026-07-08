# nova — AI Coding Agent Protocol

This file is read by AI coding assistants (Claude Code, Aider, etc.) working
in this repo. The companion to `backend/CLAUDE.md` (which is the broader
project instructions for humans). Read both before making changes.

## Cross-project isolation — HARD RULE

This repo MUST NOT contain literal references to:

- **`ali-kernel`** / **`ali_kernel`** / **`ali kernel`** — the local LLM
  gateway service we consume. We talk to it via OpenAI-compatible HTTP
  (URL is in the runtime_models.yaml), not by importing its code.
- **`ornith`** — the model name ali-kernel happens to wrap. Allowed only as a
  literal model identifier inside a string (e.g. `"model": "ornith"` in the
  benchmark JSON) or the actual GGUF file path on disk. Never as an
  identifier, function name, or comment.

The `deerflow` Python package namespace IS allowed (it's the historical
package name — Nova was forked from DeerFlow). So `from deerflow.x import y`
is fine — it's an internal import, not a cross-project reference.

"DeerFlow" and "nova" themselves are SELF references (Nova was renamed
from DeerFlow). Allowed freely in user-facing strings, comments, URLs, and
i18n keys.

## Why this matters

- **Future AI drift**: AI sessions will see references and assume they are
  load-bearing, then build more coupling on top of them.
- **Deployment decoupling**: nova should be usable with any local LLM
  stack — the gateway is configured at deployment time via runtime_models.yaml,
  not at code time via ali-kernel-specific imports.
- **Reusability**: keeping the watchdog code generic means it can monitor any
  local stack.

## Mechanical enforcement

A test (`backend/tests/test_no_cross_references.py`) runs as part of the
standard pytest suite. It fails on any forbidden string. CI
(`.github/workflows/cross-ref.yml`) runs the same test on every push/PR.

Run it locally:
```bash
python3 backend/tests/test_no_cross_references.py
# or via pytest:
cd backend && PYTHONPATH=../scripts uv run pytest tests/test_no_cross_references.py -v
```

The auto-fix tool (`scripts/fix_cross_references.py`) handles common
patterns automatically:
```bash
python3 scripts/fix_cross_references.py --dry-run   # preview
python3 scripts/fix_cross_references.py             # apply
```

## AI self-test protocol

Before EVERY commit in this repo:

```bash
cd backend && PYTHONPATH=../scripts \
  uv run pytest tests/test_healthcheck_daemon.py tests/test_no_cross_references.py -v
cd frontend && pnpm check && pnpm test
git status                       # working tree clean
```

All must succeed. If cross-ref-check finds a violation:

1. If it's in a comment or doc → just edit, remove the string, commit.
2. If it's in a real code path (variable name, function name, string
   literal that shouldn't reference the other project) → use
   `fix_cross_references.py` first; if not covered, manually rename +
   refactor.
3. If you genuinely need to keep the string (e.g. `ornith` as a model id
   literal in a benchmark JSON), add the file path to the test's allow-list
   AND explain in the commit message why.

## Adding a new feature

1. Write the test first (TDD).
2. Make it generic — would this work with a different local LLM stack?
3. If you find yourself wanting to name a specific consumer, stop. Express
   the integration through runtime_models.yaml entries or env vars instead.
4. Run all checks.
5. Commit.

## Operational gotchas

- The watchdog probes the local LLM gateway via env vars:
  - `LOCAL_LLM_GATEWAY_HOST` (default 127.0.0.1)
  - `LOCAL_LLM_GATEWAY_PORT` (default 9000)
- Binary attestation probe is opt-in (set `WATCHDOG_ATTESTATION_BINARY_PATH`).
- The bridge lives in its own repo at `~/Desktop/llama-bridge/`. Don't bring
  bridge code back into nova — point at it via `LOCAL_LLM_BRIDGE_SCRIPT`.

## Reference

- `backend/CLAUDE.md` — broader project rules
- `frontend/CLAUDE.md` — frontend rules
- `NOVA_CHANGELOG.md` — the canonical changelog and plan
- `NOVA_VS_DEERFLOW.md` — verified attribution map
