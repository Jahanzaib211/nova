"""Speech engine registry.

Built on the *generic* reflection layer (``resolve_class``), not the model
factory: ``models/factory.py`` hard-validates every entry against
``BaseChatModel``, so speech engines cannot be declared there. This mirrors how
``sandbox.use`` is configured instead.

Config shape (``config.yaml``):

```yaml
speech:
  enabled: true
  stt:
    use: deerflow.speech.engines.faster_whisper_stt:FasterWhisperSTT
    model: base
    compute_type: int8
  tts:
    use: deerflow.speech.engines.kokoro_tts:KokoroTTS
    voice: af_heart
```

Everything is optional. With no ``speech:`` section the voice endpoints report
themselves unavailable rather than erroring, so an operator who never wanted
voice is unaffected.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from pathlib import Path
from typing import Any

from deerflow.reflection import resolve_class
from deerflow.speech.base import SpeechEngineUnavailable, SpeechToText, TextToSpeech

logger = logging.getLogger(__name__)

DEFAULT_STT = "deerflow.speech.engines.faster_whisper_stt:FasterWhisperSTT"
DEFAULT_TTS = "deerflow.speech.engines.kokoro_tts:KokoroTTS"

# Engines hold model weights — tens to hundreds of MB — so they are process
# singletons rather than per-request. Guarded because WebSocket sessions arrive
# concurrently and two threads must not both pay the load cost.
_lock = threading.Lock()
_stt: SpeechToText | None = None
_tts: TextToSpeech | None = None


def _speech_config() -> dict[str, Any]:
    try:
        from deerflow.config import get_app_config

        raw = getattr(get_app_config(), "speech", None)
    except Exception:
        return {}
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    dump = getattr(raw, "model_dump", None)
    return dump() if callable(dump) else {}


def overrides_path() -> Path:
    """Where UI-written voice settings live.

    Deliberately **not** ``config.yaml``. That file is operator-owned and
    heavily commented, and there is no way to rewrite it from Python without
    either destroying every comment (``yaml.dump``) or taking on a round-trip
    YAML dependency for the sake of one endpoint. Keeping the settings panel's
    output in its own file means config.yaml is never touched, the two sources
    stay legible, and "reset to defaults" is just deleting a file.

    Precedence: this file wins over ``config.yaml``, key by key.
    """
    home = os.environ.get("DEER_FLOW_HOME")
    base = Path(home) if home else Path(".deer-flow")
    return base / "voice-settings.yaml"


def _load_overrides() -> dict[str, Any]:
    path = overrides_path()
    try:
        if not path.is_file():
            return {}
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        # A corrupt override must not take voice down — config.yaml still applies.
        logger.warning("could not read %s; ignoring voice overrides", path, exc_info=True)
        return {}


def _merged_config() -> dict[str, Any]:
    """config.yaml's ``speech:`` section with the UI overrides layered on top.

    One level of merge, not a deep merge: ``stt``/``tts``/``vad`` are replaced
    wholesale when present. A deep merge would let a stale key from config.yaml
    survive inside a section the user just rewrote, which is exactly the kind of
    invisible state that makes a settings panel untrustworthy.
    """
    merged = dict(_speech_config())
    for key, value in _load_overrides().items():
        merged[key] = value
    return merged


def save_overrides(settings: dict[str, Any]) -> Path:
    """Persist UI settings and drop the cached engines so they take effect.

    The reset is not optional. Engines are process singletons, so without it the
    panel writes a file, reports success, and every subsequent request keeps
    using the engine built at startup — the change appears to work and does
    nothing at all.
    """
    import yaml

    from deerflow.utils.atomic_write import atomic_write_text

    path = overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, yaml.safe_dump(settings, default_flow_style=False, allow_unicode=True, sort_keys=False))
    reset_engines()
    return path


def clear_overrides() -> None:
    """Reset to whatever ``config.yaml`` says."""
    with contextlib.suppress(FileNotFoundError):
        overrides_path().unlink()
    reset_engines()


def is_speech_enabled() -> bool:
    """Voice is opt-in; absent config means off, not broken."""
    cfg = _merged_config()
    return bool(cfg.get("enabled", False))


def _build(section: str, default_use: str, base: type) -> Any:
    cfg = dict(_merged_config().get(section) or {})
    use = cfg.pop("use", None) or default_use
    cfg.pop("enabled", None)
    engine_cls = resolve_class(use, base)
    try:
        return engine_cls(**cfg)
    except TypeError as e:
        # A stray config key is a config bug — say which section, don't just
        # surface an opaque constructor TypeError.
        raise SpeechEngineUnavailable(f"speech.{section} config does not match {use}: {e}") from e


def get_stt_engine() -> SpeechToText:
    global _stt
    if _stt is not None:
        return _stt
    with _lock:
        if _stt is None:
            _stt = _build("stt", DEFAULT_STT, SpeechToText)
        return _stt


def get_tts_engine() -> TextToSpeech:
    global _tts
    if _tts is not None:
        return _tts
    with _lock:
        if _tts is None:
            _tts = _build("tts", DEFAULT_TTS, TextToSpeech)
        return _tts


def set_engines(stt: SpeechToText | None = None, tts: TextToSpeech | None = None) -> None:
    """Inject engines directly. Used by tests and by the null/dev configuration."""
    global _stt, _tts
    with _lock:
        if stt is not None:
            _stt = stt
        if tts is not None:
            _tts = tts


def reset_engines() -> None:
    """Drop cached engines, releasing their weights."""
    global _stt, _tts
    with _lock:
        for engine in (_stt, _tts):
            if engine is not None:
                try:
                    engine.close()
                except Exception:
                    logger.debug("speech engine close failed", exc_info=True)
        _stt = None
        _tts = None
