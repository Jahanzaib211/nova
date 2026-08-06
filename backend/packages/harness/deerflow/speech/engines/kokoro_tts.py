"""Text-to-speech via Kokoro-82M through onnxruntime.

Kokoro is the quality/size sweet spot in open-source TTS right now — 82M
parameters, Apache-2.0, and an ONNX build that means no PyTorch anywhere in the
gateway image. That last point is why it was chosen over Coqui XTTS (heavy,
non-commercial licence) and Chatterbox (torch).

Synthesis is chunked *by sentence* and streamed. That is what makes barge-in
feel instant: when the caller stops consuming, we abandon the rest of the reply
instead of synthesizing a paragraph nobody will hear.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from collections.abc import AsyncIterator

from deerflow.speech.base import AudioChunk, SpeechEngineUnavailable, TextToSpeech

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def split_for_speech(text: str, max_chars: int = 240) -> list[str]:
    """Split into speakable pieces: sentences, with over-long ones hard-wrapped.

    Sentence-sized pieces keep prosody natural while still letting the first
    audio arrive quickly — synthesizing an entire reply before playing any of it
    is what makes assistants feel laggy.
    """
    pieces: list[str] = []
    for raw in _SENTENCE_SPLIT.split(text or ""):
        sentence = raw.strip()
        if not sentence:
            continue
        while len(sentence) > max_chars:
            cut = sentence.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            pieces.append(sentence)
    return pieces


class KokoroTTS(TextToSpeech):
    name = "kokoro"

    def __init__(
        self,
        voice: str | None = None,
        model_path: str | None = None,
        voices_path: str | None = None,
        speed: float = 1.0,
        device: str | None = None,
    ) -> None:
        self.voice = voice or os.environ.get("DEERFLOW_TTS_VOICE", "af_heart")
        self.speed = speed
        self.sample_rate = 24_000
        self._model_path = model_path or os.environ.get("DEERFLOW_TTS_MODEL_PATH")
        self._voices_path = voices_path or os.environ.get("DEERFLOW_TTS_VOICES_PATH")
        self.device = device or os.environ.get("DEERFLOW_TTS_DEVICE", "auto")
        # Set once the session is built, so callers (and the status probe) can
        # report where synthesis is *actually* running rather than what was asked for.
        self.resolved_device = "cpu"
        self._engine = None
        self._lock = threading.Lock()

    def _build_session(self):
        """Build the ONNX session ourselves, with providers we choose.

        `kokoro_onnx` decides its own providers by calling
        ``importlib.util.find_spec("onnxruntime-gpu")`` — that is a *distribution*
        name, not a module name, and hyphens cannot appear in module names, so it
        can never match. The result is that Kokoro silently stays on CPU even
        when onnxruntime-gpu is correctly installed. Its `ONNX_PROVIDER` env var
        is a global override and would drag every other ONNX model along with it.

        `Kokoro.from_session()` is the clean way out: we construct the session
        with an explicit provider list and hand it over.
        """
        import onnxruntime as ort

        from deerflow.speech.devices import resolve_onnx_providers

        providers, resolved = resolve_onnx_providers(self.device, subsystem="kokoro-tts")
        self.resolved_device = resolved

        opts = ort.SessionOptions()
        return ort.InferenceSession(self._model_path, sess_options=opts, providers=providers)

    def _ensure_engine(self):
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            try:
                from kokoro_onnx import Kokoro
            except ImportError as e:
                raise SpeechEngineUnavailable("kokoro-onnx is not installed. Install it with:\n    cd backend && uv sync --extra voice") from e
            if not self._model_path or not self._voices_path:
                raise SpeechEngineUnavailable("Kokoro needs model weights. Set DEERFLOW_TTS_MODEL_PATH and DEERFLOW_TTS_VOICES_PATH,\nor run: scripts/fetch-voice-models.sh")
            try:
                self._engine = Kokoro.from_session(self._build_session(), self._voices_path)
            except Exception as e:
                # from_session reads session._model_path, a private attribute.
                # If a future onnxruntime drops it, fall back to Kokoro's own
                # constructor (CPU) rather than losing speech altogether.
                logger.warning("Kokoro.from_session failed (%s); falling back to the default CPU constructor", e)
                self._engine = Kokoro(self._model_path, self._voices_path)
                self.resolved_device = "cpu"
            return self._engine

    def warmup(self) -> None:
        self._ensure_engine()

    async def synthesize(self, text: str, *, voice: str | None = None) -> AsyncIterator[AudioChunk]:
        engine = self._ensure_engine()
        chosen = voice or self.voice
        for piece in split_for_speech(text):
            # Inference is CPU-bound and blocking; keep it off the event loop or
            # it stalls every other WebSocket on this worker.
            samples, sample_rate = await asyncio.to_thread(engine.create, piece, voice=chosen, speed=self.speed, lang="en-us")
            yield AudioChunk(pcm=_float_to_pcm16(samples), sample_rate=int(sample_rate))

    def close(self) -> None:
        self._engine = None


def _float_to_pcm16(samples) -> bytes:
    """Kokoro emits float32 in [-1, 1]; the wire format is PCM16."""
    try:
        import numpy as np

        clipped = np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0)
        return (clipped * 32767.0).astype("<i2").tobytes()
    except ImportError:  # pragma: no cover - numpy ships with the voice extra
        import struct

        return b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
