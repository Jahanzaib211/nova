"""Speech-to-text via faster-whisper (Whisper on CTranslate2).

Chosen over `openai-whisper` because it is roughly 4x faster and far leaner for
the same weights, and over `SpeechRecognition` because that library's default
recognizer silently posts your audio to a Google endpoint — which is the
opposite of what a self-hosted voice stack is for.

Weights download once on first use and are cached; the model is loaded lazily so
importing this module (which the registry does at config-parse time) never pays
for it.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import wave

from deerflow.speech.base import AudioChunk, SpeechEngineUnavailable, SpeechToText, Transcript

logger = logging.getLogger(__name__)


class FasterWhisperSTT(SpeechToText):
    name = "faster-whisper"

    def __init__(
        self,
        model: str | None = None,
        compute_type: str | None = None,
        device: str = "cpu",
        language: str | None = None,
        download_root: str | None = None,
    ) -> None:
        self.model_size = model or os.environ.get("DEERFLOW_STT_MODEL", "base")
        self.compute_type = compute_type or os.environ.get("DEERFLOW_STT_COMPUTE", "int8")
        self.device = device
        self.language = language or os.environ.get("DEERFLOW_STT_LANGUAGE") or None
        self.download_root = download_root or os.environ.get("DEERFLOW_VOICE_MODEL_DIR")
        self._model = None
        # Model load is not thread-safe and is expensive; serialize it.
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise SpeechEngineUnavailable("faster-whisper is not installed. Install it with:\n    cd backend && uv sync --extra voice") from e
            logger.info("loading faster-whisper model=%s compute=%s", self.model_size, self.compute_type)
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.download_root,
            )
            return self._model

    def warmup(self) -> None:
        self._ensure_model()

    def transcribe(self, audio: AudioChunk) -> Transcript:
        model = self._ensure_model()
        # faster-whisper takes a file-like; wrapping raw PCM in a WAV header is
        # cheaper and more predictable than adding a decoder dependency.
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(audio.sample_rate)
            wav.writeframes(audio.pcm)
        buf.seek(0)

        segments, info = model.transcribe(buf, language=self.language, vad_filter=False, beam_size=1)
        collected = []
        text_parts = []
        for seg in segments:
            text_parts.append(seg.text)
            collected.append({"start": seg.start, "end": seg.end, "text": seg.text})
        return Transcript(
            text="".join(text_parts).strip(),
            language=getattr(info, "language", None),
            confidence=getattr(info, "language_probability", None),
            is_final=True,
            segments=collected,
        )

    def close(self) -> None:
        self._model = None
