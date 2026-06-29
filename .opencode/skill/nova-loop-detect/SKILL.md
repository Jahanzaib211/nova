---
name: nova-loop-detect
description: Diagnose and fix a loop-detection firing in Nova's agent run. Use when the user reports "the agent keeps doing the same thing", "agent is stuck in ENOENT hell", or "we hit loop detection again" — also when reading the run's `[self-test]` lines shows repeated identical tool calls or ENOENT-style grep/searches. Triages across Layer 1 (hash-based), Layer 2 (per-tool frequency), Layer 3 (dead-end search divergence) per `backend/packages/harness/deerflow/agents/middlewares/loop_detection_middleware.py:837` and recommends the next code change. Triggers on phrases like "loop fired", "agent is repeating", "ENOENT pattern", or after observing a loop in the Activity tab.
---

# nova-loop-detect

## Triage flow

1. `cd backend && uv run pytest tests/test_loop_detector_dead_end.py tests/test_loop_detection_middleware.py -q` — confirm the failure isn't a test regression.

2. Pull the run's `[middleware:loop_detection]` events from `RunEventStore`:
   `jq '.events[] | select(.category=="middleware" and .name=="loop_detection")' backend/.deer-flow/threads/{tid}/runs/{rid}.jsonl`

3. Classify the layer (per `loop_detection_middleware.py:837`):
   - **Layer 1 (line 423-450):** identical `(name, args)` call repeated. Fix: usually a tool returning a non-deterministic id (e.g., random uuid in the response). Add a normalizer to the hash.
   - **Layer 2 (line 452-489):** per-tool frequency cap (default 30 warn / 50 hard). Fix: tighten the per-tool cap in `config.yaml:loop_detection.tool_freq_overrides` (or, if legitimately needed, raise the cap).
   - **Layer 3 (line 491-594):** ENOENT pattern in greps/searches. Fix: the agent is hallucinating paths. Recommend `ask_clarification` (the middleware forces this at `_DEAD_END_HARD_LIMIT=8`).

4. Check the loop count: `loop_counter >= 5` warns; `>= 8` hard-stops + forces `ask_clarification` (`loop_detection_middleware.py:_DEAD_END_WARN_THRESHOLD`, `_DEAD_END_HARD_LIMIT`).

## Tests

`backend/tests/test_loop_detector_dead_end.py` (15 tests) and `backend/tests/test_loop_detection_middleware.py` (66 tests) cover the layers. The 15 Layer-3 tests in the dead_end file are the most relevant for ENOENT debugging.

## Anti-patterns

- ❌ **Don't** raise the `_DEAD_END_HARD_LIMIT` to silence the symptom. Fix the agent's reasoning or improve the tool's response shape.
- ❌ **Don't** add a `loop_count` field to the prompt — the middleware already injects forced final-answer messages at the budget.
- ❌ **Don't** disable loop detection in `config.yaml` (`loop_detection.enabled: false`) — that hides every other safety check too.
