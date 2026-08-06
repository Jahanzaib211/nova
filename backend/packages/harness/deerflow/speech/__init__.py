"""Nova speech: self-hosted STT and TTS behind a pluggable interface.

Everything runs locally — no API key, no per-minute cost, no audio leaving the
machine. See ``base.py`` for why these are not declared as ``models:``.
"""

from deerflow.speech.base import (
    STT_SAMPLE_RATE,
    AudioChunk,
    SpeechEngineUnavailable,
    SpeechToText,
    TextToSpeech,
    Transcript,
)
from deerflow.speech.registry import (
    get_stt_engine,
    get_tts_engine,
    is_speech_enabled,
    reset_engines,
    set_engines,
)
from deerflow.speech.vad import EnergyVad, SileroVad, Vad, VadConfig, VadEvent, frames_of

__all__ = [
    "STT_SAMPLE_RATE",
    "AudioChunk",
    "EnergyVad",
    "SileroVad",
    "SpeechEngineUnavailable",
    "SpeechToText",
    "TextToSpeech",
    "Transcript",
    "Vad",
    "VadConfig",
    "VadEvent",
    "frames_of",
    "get_stt_engine",
    "get_tts_engine",
    "is_speech_enabled",
    "reset_engines",
    "set_engines",
]
