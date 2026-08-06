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
        device: str | None = None,
        language: str | None = None,
        download_root: str | None = None,
    ) -> None:
        self.model_size = model or os.environ.get("DEERFLOW_STT_MODEL", "base")
        # `device` is resolved lazily at load time, not here: CTranslate2 can see
        # a CUDA device while still failing to load one (its CUDA runtime libs
        # are a separate install), and the honest answer is only known after a
        # load attempt. `resolved_device` carries what actually happened.
        self.device = device or os.environ.get("DEERFLOW_STT_DEVICE", "auto")
        self.resolved_device = "cpu"
        # Deliberately not defaulted here — the right compute type depends on the
        # device, and int8 only makes sense as a CPU concession.
        self._configured_compute = compute_type or os.environ.get("DEERFLOW_STT_COMPUTE") or None
        self.compute_type = self._configured_compute or "int8"
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
            from deerflow.speech.devices import default_compute_type, resolve_ct2_device

            device = resolve_ct2_device(self.device, subsystem="faster-whisper")
            compute = default_compute_type(device, self._configured_compute)
            logger.info("loading faster-whisper model=%s device=%s compute=%s", self.model_size, device, compute)
            try:
                self._model = WhisperModel(self.model_size, device=device, compute_type=compute, download_root=self.download_root)
            except Exception as e:
                if device != "cuda":
                    raise
                # CTranslate2 reports a CUDA device from the driver alone, but
                # links its own CUDA 12 runtime — on a host with only CUDA 13
                # installed this fails at load with a `libcublas.so.12` error.
                # Losing speech entirely over that would be a worse outcome than
                # running slowly, so fall back, loudly.
                logger.warning(
                    "faster-whisper failed to load on CUDA (%s); falling back to CPU. "
                    "If this is the CUDA runtime, install the GPU extra: uv sync --extra voice-gpu",
                    e,
                )
                device = "cpu"
                compute = default_compute_type(device, self._configured_compute)
                self._model = WhisperModel(self.model_size, device=device, compute_type=compute, download_root=self.download_root)

            self.resolved_device = device
            self.compute_type = compute
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
