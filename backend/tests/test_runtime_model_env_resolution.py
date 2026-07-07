from __future__ import annotations

from pathlib import Path

import yaml

from deerflow.config.app_config import AppConfig


def _write(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def _api_key(model) -> str | None:
    # api_key is an `extra` field on ModelConfig (extra="allow").
    return getattr(model, "api_key", None)


def test_runtime_model_resolves_env_placeholder_in_api_key(tmp_path, monkeypatch):
    """A UI-added (runtime) model with api_key: $VAR must resolve from the env,
    exactly like config.yaml models — never pass the literal '$VAR' downstream."""
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "models": [
                {"name": "base", "use": "langchain_openai:ChatOpenAI", "model": "gpt-test"}
            ],
        },
    )
    # Runtime store is a sibling of config.yaml.
    _write(
        tmp_path / "runtime_models.yaml",
        {
            "models": [
                {
                    "name": "fireworks-amd",
                    "use": "langchain_openai:ChatOpenAI",
                    "model": "accounts/fireworks/models/llama-v4-maverick",
                    "base_url": "https://api.fireworks.ai/inference/v1",
                    "api_key": "$FIREWORKS_TEST_KEY",
                }
            ]
        },
    )

    monkeypatch.setenv("FIREWORKS_TEST_KEY", "fw-secret-123")

    config = AppConfig.from_file(str(config_path))

    model = config.get_model_config("fireworks-amd")
    assert model is not None, "runtime model should be merged"
    assert _api_key(model) == "fw-secret-123", "api_key $VAR must resolve from env"


def test_runtime_model_with_unset_env_is_skipped_not_crashing(tmp_path, monkeypatch):
    """An unresolved $VAR in a runtime entry is skipped with a warning — it must
    not raise and abort the whole config load."""
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "models": [
                {"name": "base", "use": "langchain_openai:ChatOpenAI", "model": "gpt-test"}
            ],
        },
    )
    _write(
        tmp_path / "runtime_models.yaml",
        {
            "models": [
                {
                    "name": "needs-missing-key",
                    "use": "langchain_openai:ChatOpenAI",
                    "model": "m",
                    "api_key": "$DEFINITELY_UNSET_KEY_XYZ",
                }
            ]
        },
    )
    monkeypatch.delenv("DEFINITELY_UNSET_KEY_XYZ", raising=False)

    config = AppConfig.from_file(str(config_path))

    # Load succeeds; the base model survives, the bad runtime entry is dropped.
    assert config.get_model_config("base") is not None
    assert config.get_model_config("needs-missing-key") is None


def test_runtime_model_literal_api_key_unchanged(tmp_path):
    """A literal (non-$) api_key must pass through untouched — no regression."""
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "models": [
                {"name": "base", "use": "langchain_openai:ChatOpenAI", "model": "gpt-test"}
            ],
        },
    )
    _write(
        tmp_path / "runtime_models.yaml",
        {
            "models": [
                {
                    "name": "literal-key",
                    "use": "langchain_openai:ChatOpenAI",
                    "model": "m",
                    "api_key": "not-needed",
                }
            ]
        },
    )

    config = AppConfig.from_file(str(config_path))

    model = config.get_model_config("literal-key")
    assert model is not None
    assert _api_key(model) == "not-needed"
