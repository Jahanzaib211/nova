"""Runtime model store — model entries added through the gateway API at runtime.

Kept in a separate YAML file next to config.yaml so programmatic writes never
touch the hand-maintained config file: env placeholders (``$VAR``) and comments
in config.yaml survive, and ownership stays unambiguous — the wizard and hand
edits own config.yaml, the models API owns this file.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

RUNTIME_MODELS_ENV = "DEER_FLOW_RUNTIME_MODELS_PATH"
RUNTIME_MODELS_FILENAME = "runtime_models.yaml"


def runtime_models_path(config_path: Path | str | None = None) -> Path:
    """Resolve the runtime models file path.

    Priority: ``DEER_FLOW_RUNTIME_MODELS_PATH`` env var, then
    ``$DEER_FLOW_HOME/runtime_models.yaml`` (set in the Docker images, where it
    lives on a bind-mounted volume), then a sibling of the resolved config.yaml.
    """
    env_path = os.getenv(RUNTIME_MODELS_ENV)
    if env_path:
        return Path(env_path)
    home = os.getenv("DEER_FLOW_HOME")
    if home:
        return Path(home) / RUNTIME_MODELS_FILENAME
    if config_path is None:
        from deerflow.config.app_config import AppConfig

        config_path = AppConfig.resolve_config_path()
    return Path(config_path).parent / RUNTIME_MODELS_FILENAME


def load_runtime_model_dicts(path: Path | str | None = None) -> list[dict[str, Any]]:
    """Load raw runtime model entries. Missing or empty file yields []."""
    resolved = Path(path) if path is not None else runtime_models_path()
    if not resolved.exists():
        return []
    try:
        with open(resolved, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        logger.exception("Failed to read runtime models file at %s — ignoring it", resolved)
        return []
    if data is None:
        return []
    models = data.get("models") if isinstance(data, dict) else data
    if not isinstance(models, list):
        logger.warning("Runtime models file at %s has unexpected shape — ignoring it", resolved)
        return []
    return [m for m in models if isinstance(m, dict)]


def save_runtime_model_dicts(entries: list[dict[str, Any]], path: Path | str | None = None) -> Path:
    """Atomically persist runtime model entries (tmp file + rename)."""
    resolved = Path(path) if path is not None else runtime_models_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump({"models": entries}, sort_keys=False, allow_unicode=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(resolved.parent), prefix=".runtime_models_", suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, resolved)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return resolved


def runtime_model_names(path: Path | str | None = None) -> set[str]:
    """Names of all models currently in the runtime store."""
    return {str(m["name"]) for m in load_runtime_model_dicts(path) if "name" in m}
