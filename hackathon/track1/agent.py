#!/usr/bin/env python3
"""Track 1 — General-Purpose AI Agent (AMD Developer Hackathon Act II).

Reads /input/tasks.json, answers each task through a Fireworks AI model (served
on AMD Instinct MI300X), writes /output/results.json.

Scoring is an accuracy gate first, then token efficiency (fewer tokens = higher
rank). Design consequences, both encoded here:
  * one model call per task — no multi-agent graph, no chain-of-thought;
  * a terse system prompt instructing direct, minimal answers.

Everything is read from the environment the harness injects — no hardcoded keys,
model IDs, or answers:
  FIREWORKS_API_KEY   provided by the harness
  FIREWORKS_BASE_URL  all calls must go through this
  ALLOWED_MODELS      comma-separated permitted model IDs (pick from these only)
Optional: TRACK1_MODEL (must be in ALLOWED_MODELS), TRACK1_MAX_TOKENS,
TRACK1_CONCURRENCY, INPUT_PATH, OUTPUT_PATH.
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

# Terse by construction: no preamble, no restated question, no explanation unless
# the task itself asks for reasoning. This is the main token-efficiency lever.
SYSTEM_PROMPT = (
    "You are a precise assistant. Answer the task directly and correctly. "
    "Output only the answer — no preamble, no restatement of the question, no "
    "explanation unless the task explicitly asks for reasoning or steps. For "
    "code, output only the code. Be complete but minimal."
)


def _select_model() -> str:
    allowed = [m.strip() for m in os.getenv("ALLOWED_MODELS", "").split(",") if m.strip()]
    if not allowed:
        raise SystemExit("ALLOWED_MODELS is empty — cannot select a model")
    preferred = os.getenv("TRACK1_MODEL", "").strip()
    if preferred:
        if preferred not in allowed:
            raise SystemExit(f"TRACK1_MODEL={preferred!r} is not in ALLOWED_MODELS {allowed}")
        return preferred
    return allowed[0]


def _client() -> OpenAI:
    base_url = os.getenv("FIREWORKS_BASE_URL")
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not base_url:
        raise SystemExit("FIREWORKS_BASE_URL is not set")
    if not api_key:
        raise SystemExit("FIREWORKS_API_KEY is not set")
    return OpenAI(base_url=base_url, api_key=api_key, timeout=25.0, max_retries=2)


def _answer_one(client: OpenAI, model: str, max_tokens: int, task: dict) -> dict:
    task_id = task.get("task_id")
    prompt = task.get("prompt", "")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=max_tokens,
        )
        answer = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # never let one task abort the batch
        print(f"task {task_id!r} failed: {e}", file=sys.stderr)
        answer = ""
    return {"task_id": task_id, "answer": answer}


def main() -> int:
    input_path = Path(os.getenv("INPUT_PATH", "/input/tasks.json"))
    output_path = Path(os.getenv("OUTPUT_PATH", "/output/results.json"))
    tasks = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise SystemExit(f"{input_path} must contain a JSON array")

    model = _select_model()
    max_tokens = int(os.getenv("TRACK1_MAX_TOKENS", "1024"))
    concurrency = max(1, int(os.getenv("TRACK1_CONCURRENCY", "4")))
    client = _client()

    print(f"Track 1: {len(tasks)} task(s) on model {model!r} (concurrency={concurrency})", file=sys.stderr)

    if concurrency == 1:
        results = [_answer_one(client, model, max_tokens, t) for t in tasks]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(lambda t: _answer_one(client, model, max_tokens, t), tasks))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(results)} result(s) to {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
