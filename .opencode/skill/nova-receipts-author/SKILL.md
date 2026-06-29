---
name: nova-receipts-author
description: Author a typed post-run receipt for a new Nova agent run or a new tool. Use when adding observability for an end-to-end workflow, when a tool produces artifacts a judge could re-verify, or when the "code is the ultimate proof" thesis needs a new structured artifact. The `Receipt` class doesn't exist yet (no hit on `class Receipt` in `backend/`); today the closest analog is `RunEventStore` (JSONL of `message/trace/lifecycle` events) plus `BUILD_JOURNAL.md` (free-form text) plus `REVIEW.md` (deterministic code-review). The schema is Pydantic v2 in `backend/packages/harness/deerflow/receipts/types.py` (planned v7.4). Triggers on phrases like "make this verifiable", "replay this run", "what did the agent do", or before shipping any new end-to-end workflow.
---

# nova-receipts-author

## The recipe: author a new receipt type

A `Receipt` is a post-run JSON artifact with:

- `id` (uuid v4)
- `thread_id`, `run_id`, `created_at` (ISO 8601 UTC)
- `prompt` (the user input, normalized)
- `model` (resolved chat model)
- `tools_called` (ordered list of `{tool, args, result_excerpt, duration_ms, ok}`)
- `diff` (unified diff of files touched)
- `test_results` (`{passed, failed, duration_s}`)
- `browser_check` (the `BrowserCheck` from `sandbox/browser_check.py:283`, when applicable)
- `receipt_version` (integer; bump on schema change)
- `signature` (HMAC-SHA256 of all preceding fields, keyed by `DEER_FLOW_RECEIPT_SIGNING_KEY`)

## Emit from a new tool

```python
from deerflow.receipts import Receipt, emit_receipt

async def my_new_tool(thread_id: str, **kwargs) -> str:
    start = time.monotonic()
    try:
        result = await _do_work()
        emit_receipt(
            thread_id=thread_id,
            tool="my_new_tool",
            args=kwargs,
            result_excerpt=result[:200],
            duration_ms=int((time.monotonic() - start) * 1000),
            ok=True,
        )
        return result
    except Exception as e:
        emit_receipt(
            thread_id=thread_id,
            tool="my_new_tool",
            args=kwargs,
            result_excerpt=str(e)[:200],
            duration_ms=int((time.monotonic() - start) * 1000),
            ok=False,
        )
        raise
```

## Re-verify a receipt

```bash
cd backend
PYTHONPATH=. uv run python -m nova_cli verify <receipt-id>
# or, for a recorded session:
nova verify backend/tests/fixtures/replay/write_read_file.ultra.json
```

## Replay-vs-fresh semantic

A receipt is **not** a record-and-replay artifact by itself (that's `RunEventStore`). A receipt is a *summary* of one; `nova verify` re-runs the run from the receipt + the deterministic tool environment and asserts the outcome matches.

## Anti-patterns

- ❌ **Don't** build a receipt that includes the full tool output (>1MB). Excerpt + checksum only.
- ❌ **Don't** sign receipts with the model's API key — that's a credential leak. Use `DEER_FLOW_RECEIPT_SIGNING_KEY` (32+ bytes, rotated quarterly).
- ❌ **Don't** store receipts inside the thread dir (use `RunEventStore` in `.deer-flow/threads/{tid}/runs/{rid}.jsonl` and have receipts reference the run_id).
