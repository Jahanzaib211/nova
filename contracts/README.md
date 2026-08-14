# contracts

Wire contracts and schema definitions used by Nova's backend.

## Files

| File | Description |
|---|---|
| `custom_events_contract.json` | SSE custom event schema (`task_started`, `task_completed`, etc.) |

## Adding a new contract

1. Create or update the JSON file here.
2. Add a test in `backend/tests/test_custom_events_contract.py`.
3. Update this README.
