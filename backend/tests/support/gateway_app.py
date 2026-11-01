"""Shared harness for HTTP e2e tests that boot the real Gateway app.

The ``isolated_deer_flow_home`` / ``isolated_app`` fixtures in
``tests/conftest.py`` are thin wrappers over the functions here, so every e2e
module (``tests/`` and ``tests/integration/``) builds the app the same way
instead of carrying its own copy.

A module that needs a different staged ``config.yaml`` overrides the
``isolated_gateway_config_yaml`` fixture; everything else is shared.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

MINIMAL_GATEWAY_CONFIG_YAML = """\
log_level: info
models:
  - name: fake-test-model
    display_name: Fake Test Model
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: $OPENAI_API_KEY
    base_url: $OPENAI_API_BASE
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
agents_api:
  enabled: true
title:
  enabled: false
memory:
  enabled: false
database:
  backend: sqlite
run_events:
  backend: memory
"""


def stage_isolated_deer_flow_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, config_yaml: str) -> Path:
    """Stand up an isolated DeerFlow data root + hermetic config under ``tmp_path``.

    - ``DEER_FLOW_HOME`` points under ``tmp_path``, not the real ``.deer-flow``.
    - A minimal ``config.yaml`` and an empty ``extensions_config.json`` are
      written and pinned via ``DEER_FLOW_CONFIG_PATH`` /
      ``DEER_FLOW_EXTENSIONS_CONFIG_PATH`` so lifespan boot does not depend on
      the developer's local config layout (CI only ships the example config).
    - ``OPENAI_API_KEY`` / ``OPENAI_API_BASE`` get placeholders because the
      config references them and they resolve at parse time; the LLM itself is
      always faked by the tests.
    """
    home = tmp_path / "deer-flow-home"
    home.mkdir()
    monkeypatch.setenv("DEER_FLOW_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-not-used")
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.invalid")

    staged_config = tmp_path / "config.yaml"
    staged_config.write_text(config_yaml, encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(staged_config))

    staged_extensions_config = tmp_path / "extensions_config.json"
    staged_extensions_config.write_text('{"mcpServers": {}, "skills": {}}', encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(staged_extensions_config))
    return home


def reset_process_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear runtime singletons that depend on this test's temporary config.

    The Gateway app/lifespan path reads process-wide caches before wiring
    request-scoped dependencies. The staged ``config.yaml`` /
    ``extensions_config.json`` / ``DEER_FLOW_HOME`` only take effect once these
    are reset:

    - app_config / extensions_config: parsed config file caches.
    - paths: ``DEER_FLOW_HOME``-derived filesystem paths.
    - persistence.engine: SQLAlchemy engine/session factory for the sqlite dir.
    - app.gateway.deps: cached local auth provider/repository.

    ``raising=False`` keeps this resilient if one of the attributes is renamed;
    the next ``get_app_config()``/``get_paths()`` call surfaces the real
    incompatibility loudly.
    """
    from app.gateway import deps as deps_module
    from deerflow.config import app_config as app_config_module
    from deerflow.config import extensions_config as extensions_config_module
    from deerflow.config import paths as paths_module
    from deerflow.persistence import engine as engine_module

    for module, attr, value in (
        (app_config_module, "_app_config", None),
        (app_config_module, "_app_config_path", None),
        (app_config_module, "_app_config_mtime", None),
        (app_config_module, "_app_config_is_custom", False),
        (extensions_config_module, "_extensions_config", None),
        (paths_module, "_paths_singleton", None),
        (paths_module, "_paths", None),
        (engine_module, "_engine", None),
        (engine_module, "_session_factory", None),
        (deps_module, "_cached_local_provider", None),
        (deps_module, "_cached_repo", None),
    ):
        monkeypatch.setattr(module, attr, value, raising=False)


def preserve_process_config_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore config singletons mutated as a side effect of AppConfig loading.

    ``AppConfig.from_file()`` calls ``_apply_singleton_configs()``, which pushes
    nested config sections into module-level caches used by middlewares, tool
    selection, and runtime providers. Snapshotting those attributes with
    ``monkeypatch`` lets pytest restore the pre-test values during teardown, so
    loading the isolated test config does not leak into later tests.
    """
    from deerflow.config import (
        acp_config,
        agents_api_config,
        checkpointer_config,
        guardrails_config,
        memory_config,
        stream_bridge_config,
        subagents_config,
        summarization_config,
        title_config,
        tool_search_config,
    )

    for module, attr in (
        (title_config, "_title_config"),
        (summarization_config, "_summarization_config"),
        (memory_config, "_memory_config"),
        (agents_api_config, "_agents_api_config"),
        (subagents_config, "_subagents_config"),
        (tool_search_config, "_tool_search_config"),
        (guardrails_config, "_guardrails_config"),
        (checkpointer_config, "_checkpointer_config"),
        (stream_bridge_config, "_stream_bridge_config"),
        (acp_config, "_acp_agents"),
    ):
        monkeypatch.setattr(module, attr, getattr(module, attr), raising=False)


def build_isolated_app(isolated_deer_flow_home: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a fresh FastAPI app whose sqlite DB lives under the isolated home."""
    preserve_process_config_singletons(monkeypatch)
    reset_process_singletons(monkeypatch)

    from deerflow.config import app_config as app_config_module

    cfg = app_config_module.get_app_config()
    cfg.database.sqlite_dir = str(isolated_deer_flow_home / "db")

    from app.gateway.app import create_app

    return create_app()


# ---------------------------------------------------------------------------
# HTTP helpers for driving the runs API through ``TestClient``.
# ---------------------------------------------------------------------------


def register_user(client, *, email: str = "runtime-e2e@example.com") -> str:
    """Register (and auto-login) a user; return the CSRF token cookie."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "very-strong-password-123"},
    )
    assert response.status_code == 201, response.text
    csrf_token = client.cookies.get("csrf_token")
    assert csrf_token
    return csrf_token


def create_thread(client, csrf_token: str) -> str:
    thread_id = str(uuid.uuid4())
    response = client.post(
        "/api/threads",
        json={"thread_id": thread_id, "metadata": {"purpose": "runtime-lifecycle-e2e"}},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200, response.text
    return thread_id


def run_body(**overrides) -> dict[str, Any]:
    body: dict[str, Any] = {
        "assistant_id": "lead_agent",
        "input": {"messages": [{"role": "user", "content": "Run lifecycle E2E prompt"}]},
        "config": {"recursion_limit": 50},
        "stream_mode": ["values"],
    }
    body.update(overrides)
    return body


def drain_stream(response, *, timeout: float = 10.0, max_bytes: int = 1024 * 1024) -> str:
    """Read an SSE response until ``event: end``, failing fast on stalls."""
    chunks: queue.Queue[bytes | BaseException | object] = queue.Queue()
    sentinel = object()

    def read_stream() -> None:
        try:
            for chunk in response.iter_bytes():
                chunks.put(chunk)
                if b"event: end" in chunk:
                    break
        except Exception as exc:  # pragma: no cover - reported in the main test thread
            chunks.put(exc)
        finally:
            chunks.put(sentinel)

    reader = threading.Thread(target=read_stream, daemon=True)
    reader.start()

    deadline = time.monotonic() + timeout
    body = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"SSE stream did not finish within {timeout}s; transcript tail={body[-4000:].decode('utf-8', errors='replace')}")
        try:
            chunk = chunks.get(timeout=remaining)
        except queue.Empty as exc:
            raise AssertionError(f"SSE stream did not produce data within {timeout}s; transcript tail={body[-4000:].decode('utf-8', errors='replace')}") from exc
        if chunk is sentinel:
            break
        if isinstance(chunk, BaseException):
            raise AssertionError("SSE reader failed") from chunk
        body += chunk
        if b"event: end" in body:
            break
        if len(body) >= max_bytes:
            raise AssertionError(f"SSE stream exceeded {max_bytes} bytes without event: end")
    if b"event: end" not in body:
        raise AssertionError(f"SSE stream closed before event: end; transcript tail={body[-4000:].decode('utf-8', errors='replace')}")
    return body.decode("utf-8", errors="replace")


def run_id_from_response(response) -> str:
    location = response.headers.get("content-location", "")
    assert location, "run stream response must include Content-Location"
    return location.rstrip("/").split("/")[-1]
