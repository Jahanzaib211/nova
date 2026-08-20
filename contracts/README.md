# contracts

Cross-language wire contracts. Each file here is loaded by **both** the backend
and frontend test suites, so a change to either side that breaks the agreement
fails the build rather than surfacing later as "the apps are out of sync".

## Files

| File | Description | Pinned by |
|---|---|---|
| `custom_events_contract.json` | JSON Schema for the four SSE `custom` stream-mode payloads (`task_progress`, `verify_result`, `llm_error`, `task_running`) | `backend/tests/test_custom_events_contract.py`, `frontend/tests/unit/core/threads/custom-event-contract.test.ts` |
| `subagent_status_contract.json` | The `ToolMessage.additional_kwargs.subagent_status` values and the result-text prefixes each maps from | `backend/tests/test_subagent_status_contract.py`, `frontend/tests/unit/core/tasks/subtask-result.test.ts` |

The subagent contract exists because the frontend used to derive a subtask
card's state by string-matching the leading text of the `task` tool's result.
Any rewording on the backend silently broke the card lifecycle. The structured
`subagent_status` field replaced that; the prefixes remain as a fallback for
messages predating the change, and this fixture keeps the two in agreement.

## Adding a new contract

1. Create or update the JSON file here.
2. Add a backend test that loads it (see the table for the existing ones).
3. Add a frontend test that loads the same file — a contract only one side
   reads is documentation, not a contract.
4. Update this README.
