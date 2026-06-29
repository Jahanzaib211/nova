---
name: nova-subagent-router
description: Configure per-tool-type model routing in Nova to save 40-60% of LLM tokens. Use when configuring `config.yaml` to use a cheaper model for `browser_check`, `code_review`, `system_probe`, `save_skill` while keeping the lead agent on `opus`. The `ModelRouter` class doesn't exist yet (per the audit: no matches for `tool_type|model_routing|model_for_tool`); today every tool call uses the same model binding as the lead agent. This skill teaches the v7.4 pattern: a declarative `models.routing:` block in `config.yaml` + a `router.route(tool_type) -> ChatModel` resolver that runs once at `make_lead_agent` graph build time. Triggers on phrases like "cut token costs", "use haiku for screenshots", "route browser_check to a cheaper model", or after observing token-usage breakdowns via `SubagentTokenCollector` snapshots.
---

# nova-subagent-router

## Current state (and what to change)

- `subagents/token_collector.py:50` captures per-model tokens but doesn't influence routing.
- `subagents/config.py:38` has `model: str = "inherit"` (always uses parent's model).
- The audit confirmed no `tool_type|model_routing|model_for_tool` exists.

## v7.4 plan

1. Add to `config.yaml`:

   ```yaml
   models:
     routing:
       # Map tool type → model name
       # Anything not listed falls through to the lead agent's model.
       browser_check: haiku
       code_review: sonnet
       save_skill: haiku
       system_probe: haiku
       shell_session: haiku
       shell_view: haiku
       shell_write: haiku
       shell_wait: haiku
       shell_kill: haiku
       browser_navigate: haiku
       browser_click: haiku
       browser_input: haiku
       browser_eval: haiku
       screenshot: haiku
       # lead agent stays on opus (or whatever was selected at startup)
   ```

2. Add `backend/packages/harness/deerflow/models/router.py`:

   ```python
   class ModelRouter:
       def __init__(self, lead_model: ChatModel, routing: dict[str, str], app_config):
           self.lead = lead_model
           self.routing = routing or {}
           self._cache: dict[str, ChatModel] = {}

       def route(self, tool_name: str) -> ChatModel:
           target = self.routing.get(tool_name)
           if not target or target == self.lead.get_name():
               return self.lead
           if target not in self._cache:
               self._cache[target] = create_chat_model(target, ...)
           return self._cache[target]
   ```

3. Wire at `agents/lead_agent/agent.py:519, 546` (the two `create_agent` call sites): pass the router to the tool wrappers via a contextvar or runtime override.

## Verify

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_model_router.py -v
# Expects: 5+ tests covering default fall-through, named override, unknown model error, cache hit, cache miss
```

## Anti-patterns

- ❌ **Don't** route the lead agent's model (always opus / sonnet) to anything cheaper. The orchestrator needs the full reasoning capacity.
- ❌ **Don't** route `save_skill` to a stronger model than the lead — `save_skill` is mechanical (write file + scan + register).
- ❌ **Don't** route `code_review` below sonnet — it needs reasoning about diffs.
- ❌ **Don't** route `dev_verify` — it chains `tests → browser_check → code_review`; let it use the lead's model so the budget is enforced at the right level.
