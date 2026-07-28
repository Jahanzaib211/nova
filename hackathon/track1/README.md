# Track 1 — General-Purpose AI Agent

Nova's submission for Track 1 of the AMD Developer Hackathon (Act II). One model
call per task through **Fireworks AI** (served on AMD Instinct MI300X), tuned for
the scoring order: **accuracy gate first, then token efficiency**.

## Contract

- Reads tasks from `/input/tasks.json`:

  ```json
  [ { "task_id": "t1", "prompt": "..." } ]
  ```

- Writes answers to `/output/results.json`:

  ```json
  [ { "task_id": "t1", "answer": "..." } ]
  ```

- Exit 0 on success. One task failing never aborts the batch (empty answer).

## Environment (injected by the harness — nothing hardcoded)

| Variable | Meaning |
|---|---|
| `FIREWORKS_API_KEY` | Provided by the harness — used as the client key |
| `FIREWORKS_BASE_URL` | All calls routed here |
| `ALLOWED_MODELS` | Comma-separated permitted model IDs; we pick from these only |
| `TRACK1_MODEL` (opt) | Force a specific model (must be in `ALLOWED_MODELS`) |
| `TRACK1_MAX_TOKENS` (opt) | Output cap (default 1024) |
| `TRACK1_CONCURRENCY` (opt) | Parallel tasks (default 4) |

## Token-efficiency design

- **One call per task** — no multi-agent graph, no chain-of-thought.
- **Terse system prompt** — answer only, no preamble/restatement/explanation
  unless the task asks for reasoning.
- `temperature=0` for determinism and accuracy; bounded `max_tokens`.

## Build & push (linux/amd64)

```bash
docker buildx build --platform linux/amd64 \
  -t ghcr.io/<org>/nova-track1:latest --push .
```

Or via the repo root: `make hackathon-track1 REGISTRY=ghcr.io/<org>`.

## Test locally

```bash
# Hermetic unit test (no creds, no network):
python -m pytest test_agent.py

# Real run against Fireworks:
docker run --rm \
  -e FIREWORKS_API_KEY=$FIREWORKS_API_KEY \
  -e FIREWORKS_BASE_URL=$FIREWORKS_BASE_URL \
  -e ALLOWED_MODELS=gemma-4-31b-it \
  -v "$PWD/sample:/input" -v "$PWD/out:/output" \
  nova-track1:latest
cat out/results.json
```
