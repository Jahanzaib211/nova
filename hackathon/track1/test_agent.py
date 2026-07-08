"""Hermetic smoke test for the Track 1 agent.

No network, no credentials: the `openai` module is stubbed and the client is
patched, so this verifies the harness contract (read /input, one call per task,
write valid /output, exit 0) deterministically. Run:

    python -m pytest hackathon/track1/test_agent.py
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path


def _install_fake_openai() -> None:
    """Stub `openai.OpenAI` so agent.py imports without the real dependency."""
    mod = types.ModuleType("openai")

    class _FakeOpenAI:
        def __init__(self, *a, **k):
            self.kwargs = k

    mod.OpenAI = _FakeOpenAI
    sys.modules["openai"] = mod


def _load_agent():
    _install_fake_openai()
    sys.path.insert(0, str(Path(__file__).parent))
    if "agent" in sys.modules:
        return importlib.reload(sys.modules["agent"])
    return importlib.import_module("agent")


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeClient:
    """Echoes a canned answer; records the model + messages it was called with."""

    def __init__(self):
        self.calls = []

        parent = self

        class _Completions:
            def create(self, *, model, messages, **kw):
                parent.calls.append({"model": model, "messages": messages})
                return _FakeCompletion(f"answer-for::{messages[-1]['content']}")

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_agent_reads_tasks_calls_model_and_writes_results(tmp_path, monkeypatch):
    agent = _load_agent()

    tasks = [
        {"task_id": "t1", "prompt": "What is 2+2?"},
        {"task_id": "t2", "prompt": "Capital of France?"},
    ]
    input_path = tmp_path / "tasks.json"
    output_path = tmp_path / "results.json"
    input_path.write_text(json.dumps(tasks), encoding="utf-8")

    monkeypatch.setenv("INPUT_PATH", str(input_path))
    monkeypatch.setenv("OUTPUT_PATH", str(output_path))
    monkeypatch.setenv("ALLOWED_MODELS", "gemma-4-31b-it, kimi-k2p7-code")
    monkeypatch.setenv("FIREWORKS_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("FIREWORKS_API_KEY", "fake")
    monkeypatch.setenv("TRACK1_CONCURRENCY", "1")

    fake = _FakeClient()
    monkeypatch.setattr(agent, "_client", lambda: fake)

    rc = agent.main()
    assert rc == 0

    results = json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(results, list) and len(results) == 2
    assert {r["task_id"] for r in results} == {"t1", "t2"}
    assert all("answer" in r and r["answer"] for r in results)

    # One call per task, and the first ALLOWED_MODELS entry was used.
    assert len(fake.calls) == 2
    assert all(c["model"] == "gemma-4-31b-it" for c in fake.calls)


def test_prefers_track1_model_when_allowed(tmp_path, monkeypatch):
    agent = _load_agent()
    monkeypatch.setenv("ALLOWED_MODELS", "gemma-4-31b-it,kimi-k2p7-code")
    monkeypatch.setenv("TRACK1_MODEL", "kimi-k2p7-code")
    assert agent._select_model() == "kimi-k2p7-code"


def test_rejects_model_outside_allowed(tmp_path, monkeypatch):
    agent = _load_agent()
    monkeypatch.setenv("ALLOWED_MODELS", "gemma-4-31b-it")
    monkeypatch.setenv("TRACK1_MODEL", "gpt-4o")
    try:
        agent._select_model()
        raise AssertionError("expected SystemExit for disallowed model")
    except SystemExit:
        pass


def test_failed_task_does_not_abort_batch(tmp_path, monkeypatch):
    agent = _load_agent()
    tasks = [{"task_id": "ok", "prompt": "hi"}, {"task_id": "boom", "prompt": "x"}]
    input_path = tmp_path / "tasks.json"
    output_path = tmp_path / "results.json"
    input_path.write_text(json.dumps(tasks), encoding="utf-8")
    monkeypatch.setenv("INPUT_PATH", str(input_path))
    monkeypatch.setenv("OUTPUT_PATH", str(output_path))
    monkeypatch.setenv("ALLOWED_MODELS", "gemma-4-31b-it")
    monkeypatch.setenv("FIREWORKS_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("FIREWORKS_API_KEY", "fake")
    monkeypatch.setenv("TRACK1_CONCURRENCY", "1")

    class _Boom:
        def __init__(self):
            class _Completions:
                def create(self, *, model, messages, **kw):
                    if messages[-1]["content"] == "x":
                        raise RuntimeError("simulated model error")
                    return _FakeCompletion("ok-answer")

            class _Chat:
                completions = _Completions()

            self.chat = _Chat()

    monkeypatch.setattr(agent, "_client", lambda: _Boom())

    rc = agent.main()
    assert rc == 0
    results = {r["task_id"]: r["answer"] for r in json.loads(output_path.read_text())}
    assert results["ok"] == "ok-answer"
    assert results["boom"] == ""  # failed task → empty answer, batch still completes
