---
name: nova-prompt-author
description: Edit the lead agent's system prompt with discipline. Use when the user says "the agent is being too X", "tell the agent to Y", "the prompt needs a rule about Z", or before adding any new rule to the system prompt. The system prompt is at `backend/packages/harness/deerflow/agents/lead_agent/prompt.py` (a single function that returns a `ChatPromptTemplate`). Use this skill BEFORE making the change; the skill walks through the change-and-validate cycle. Triggers on phrases like "edit the agent prompt", "add a rule to the lead agent", "system prompt", "AGENT_MANIFEST", or before merging any change touching `agents/lead_agent/prompt.py`.
---

# nova-prompt-author

## The change-and-validate cycle

1. **Read the current prompt:** `wc -l backend/packages/harness/deerflow/agents/lead_agent/prompt.py` (should be 500+ lines). Identify the section you're changing (e.g., `<skill_persistence>`, `<command_arsenal>`, `<self_verify>`, etc.).

2. **Find or create the test:** `backend/tests/test_lead_agent_prompt.py` (exists per the audit's test inventory). Add a regression assertion: e.g., "the prompt contains `<rule>no ENOENT hunts</rule>`" so the rule is enforced by the test, not just the prompt.

3. **Update the prompt.** Constraints (per `FORK_V5.md` hard rules):
   - Use **literal paths** (no `{...}` braces) — braces break `.format()` (the prompt is `.format()`-templated).
   - Keep `<system-reminder>` blocks separate from the static prompt — they're injected at runtime, not at template time.
   - Don't reference deprecated/removed tools (e.g., `cowork`, the v6 namespace).
   - Don't duplicate the `<agent_manifest>` content — the manifest is injected separately.

4. **Re-run replay E2E:** `cd backend && PYTHONPATH=. uv run pytest tests/test_replay_golden.py -v`. The fixture should still match — if it doesn't, your prompt change drifted the agent graph semantics.

5. **Update the test for the new rule** so it actually exercises it (not just asserts the rule exists).

## Anti-patterns

- ❌ **Don't** add `import` statements to the prompt file — it's a function returning a `ChatPromptTemplate`, not a module.
- ❌ **Don't** add a rule without a corresponding test. The rule lives in the prompt; the test lives in the code. Both must move together.
- ❌ **Don't** make the prompt > 2000 lines. Larger prompts slow the agent and dilute the most important rules.
- ❌ **Don't** reference tool names that are loaded via `assemble_deferred_tools` — those are HIDDEN from the model until promoted. If you mention `tool_search` in the prompt, the agent won't find the tool by its deferred name; it finds it via the `<available-deferred-tools>` block.
- ❌ **Don't** reword rules without re-running the replay E2E. The fixture is the contract.
