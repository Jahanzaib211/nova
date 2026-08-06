"""Utterance endpointing — deciding when you started and stopped talking.

This is what makes the conversation feel like a conversation rather than a
walkie-talkie, and it is what makes barge-in possible: the moment speech is
detected while Nova is talking, the caller cancels synthesis.

Two implementations behind one interface:

* ``SileroVad`` — the real one. ~2 MB ONNX model, runs on the ``onnxruntime``
  the TTS engine already needs, far more accurate than energy thresholds or
  ``webrtcvad`` (which is unmaintained and only accepts a few exact frame
  sizes).
* ``EnergyVad`` — pure-Python RMS fallback. Not as good, but it needs no model
  and no dependency, so tests and constrained deployments always have a working
  endpointer. This is what CI uses.

Both are frame-based and stateful: feed 20 ms frames in order, get transitions
out. State lives here rather than in the transport so the WebSocket handler
stays a transport.
"""

from __future__ import annotations

import abc
import array
import logging
import math
import os
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

FRAME_MS = 20
"""Frames are 20 ms — the granularity every VAD here expects."""


class VadEvent(Enum):
    """What changed after the latest frame."""

    NONE = "none"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


@dataclass
class VadConfig:
    # How much silence ends an utterance. Too short and it cuts you off
    # mid-thought; too long and Nova feels sluggish. 600ms is a common
    # conversational compromise.
    silence_ms: int = 600
    # Speech must persist this long before we call it speech, so a cough or a
    # door slam doesn't interrupt Nova.
    speech_ms: int = 160
    # Utterances longer than this are force-ended, so a stuck-open mic cannot
    # buffer without bound.
    max_utterance_ms: int = 30_000
    threshold: float = 0.5

    @classmethod
    def from_env(cls) -> VadConfig:
        def _int(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, default))
            except (TypeError, ValueError):
                return default

        return cls(
            silence_ms=_int("DEERFLOW_VAD_SILENCE_MS", 600),
            speech_ms=_int("DEERFLOW_VAD_SPEECH_MS", 160),
            max_utterance_ms=_int("DEERFLOW_VAD_MAX_UTTERANCE_MS", 30_000),
        )


class Vad(abc.ABC):
    """Frame-in, transition-out endpointer."""

    def __init__(self, config: VadConfig | None = None, sample_rate: int = 16_000) -> None:
        self.config = config or VadConfig()
        self.sample_rate = sample_rate
        self._speaking = False
        self._speech_run_ms = 0
        self._silence_run_ms = 0
        self._utterance_ms = 0

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @abc.abstractmethod
    def _frame_is_speech(self, frame: bytes) -> bool:
        """Does this single 20 ms frame contain speech?"""

    def reset(self) -> None:
        self._speaking = False
        self._speech_run_ms = 0
        self._silence_run_ms = 0
        self._utterance_ms = 0

    def accept(self, frame: bytes) -> VadEvent:
        """Feed one 20 ms PCM16 frame; get the transition it caused."""
        voiced = self._frame_is_speech(frame)

        if self._speaking:
            self._utterance_ms += FRAME_MS

        if voiced:
            self._speech_run_ms += FRAME_MS
            self._silence_run_ms = 0
        else:
            self._silence_run_ms += FRAME_MS
            self._speech_run_ms = 0

        if not self._speaking:
            if self._speech_run_ms >= self.config.speech_ms:
                self._speaking = True
                self._utterance_ms = self._speech_run_ms
                self._silence_run_ms = 0
                return VadEvent.SPEECH_START
            return VadEvent.NONE

        # Speaking: end on sustained silence, or on the hard ceiling so a stuck
        # mic can't buffer forever.
        if self._silence_run_ms >= self.config.silence_ms:
            self.reset()
            return VadEvent.SPEECH_END
        if self._utterance_ms >= self.config.max_utterance_ms:
            self.reset()
            return VadEvent.SPEECH_END
        return VadEvent.NONE


class EnergyVad(Vad):
    """RMS-threshold endpointer. No model, no dependency, always available.

    Deliberately simple. It will trigger on loud non-speech, which is why
    ``speech_ms`` exists — a transient has to persist to count.
    """

    # RMS over int16 range. ~0.02 of full scale: above room tone, below speech.
    DEFAULT_RMS_THRESHOLD = 600.0

    def __init__(self, config: VadConfig | None = None, sample_rate: int = 16_000, rms_threshold: float | None = None) -> None:
        super().__init__(config, sample_rate)
        self.rms_threshold = rms_threshold if rms_threshold is not None else self.DEFAULT_RMS_THRESHOLD

    def _frame_is_speech(self, frame: bytes) -> bool:
        if len(frame) < 2:
            return False
        samples = array.array("h")
        # array.frombytes needs an even length; drop a trailing odd byte.
        samples.frombytes(frame[: len(frame) - (len(frame) % 2)])
        if not samples:
            return False
        total = 0.0
        for s in samples:
            total += float(s) * float(s)
        rms = math.sqrt(total / len(samples))
        return rms >= self.rms_threshold


class SileroVad(Vad):
    """Silero VAD via onnxruntime — the accurate one.

    Falls back to :class:`EnergyVad`'s behaviour if the model or runtime is
    unavailable, so a missing optional dependency degrades the *quality* of
    endpointing rather than breaking voice entirely.
    """

    # Silero's exported graph has two undocumented input requirements, and
    # violating either fails *silently* — the dynamic axes accept any shape and
    # return a meaningless probability rather than raising.
    #
    #   1. Exactly one window size per sample rate: 512 samples at 16 kHz.
    #   2. The previous window's last 64 samples prepended as context, so the
    #      real input is 576 wide. The reference wrapper does this internally,
    #      which is why almost every reimplementation misses it.
    #
    # Measured on real synthesized speech: with the context prefix the model
    # peaks at p=1.00 and calls 85% of windows voiced; without it, p never
    # exceeds 0.06 on the same audio. So a VAD missing the prefix is not
    # "slightly less accurate" — it is deaf, and it fails closed in the worst
    # way: silence and noise still test fine, so unit tests look green while
    # nobody can talk to Nova at all.
    _WINDOW_SAMPLES = {16_000: 512, 8_000: 256}
    _CONTEXT_SAMPLES = {16_000: 64, 8_000: 32}

    def __init__(self, config: VadConfig | None = None, sample_rate: int = 16_000, model_path: str | None = None) -> None:
        super().__init__(config, sample_rate)
        self._session = None
        self._state = None
        self._fallback = EnergyVad(config, sample_rate)
        self._model_path = model_path or os.environ.get("DEERFLOW_VAD_MODEL_PATH")
        self._load_attempted = False
        # Frames arrive at FRAME_MS; windows leave at _window_samples. The two
        # do not divide evenly (320 vs 512), so partial audio is carried over.
        self._window_samples = self._WINDOW_SAMPLES.get(sample_rate, 0)
        self._context_samples = self._CONTEXT_SAMPLES.get(sample_rate, 0)
        self._buf = bytearray()
        self._context = None
        self._last_speech = False
        # Per-session, not per-frame: see the fallback handler below.
        self._warned_fallback = False

    def _ensure_session(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True
        if not self._model_path or not os.path.exists(self._model_path):
            return
        if not self._window_samples:
            # An unsupported rate would mean guessing a window size. The energy
            # VAD is a worse detector but an honest one; a mis-windowed Silero
            # is neither.
            return
        try:
            import numpy as np
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self._session = ort.InferenceSession(self._model_path, sess_options=opts, providers=["CPUExecutionProvider"])
            self._state = np.zeros((2, 1, 128), dtype=np.float32)
        except Exception:
            self._session = None

    def reset(self) -> None:
        super().reset()
        self._fallback.reset()
        self._buf.clear()
        self._context = None
        self._last_speech = False
        if self._session is not None:
            try:
                import numpy as np

                self._state = np.zeros((2, 1, 128), dtype=np.float32)
            except Exception:
                pass

    def _frame_is_speech(self, frame: bytes) -> bool:
        self._ensure_session()
        if self._session is None:
            return self._fallback._frame_is_speech(frame)
        try:
            import numpy as np

            self._buf.extend(frame)
            window_bytes = self._window_samples * 2

            if self._context is None:
                self._context = np.zeros(self._context_samples, dtype=np.float32)

            decided: bool | None = None
            while len(self._buf) >= window_bytes:
                window = np.frombuffer(bytes(self._buf[:window_bytes]), dtype=np.int16)
                del self._buf[:window_bytes]
                samples = window.astype(np.float32) / 32768.0
                audio = np.concatenate([self._context, samples]).reshape(1, -1)
                # Carry the tail forward before the next window — this is the
                # context requirement documented on _CONTEXT_SAMPLES.
                self._context = samples[-self._context_samples :] if self._context_samples else self._context
                out, self._state = self._session.run(
                    None,
                    {"input": audio, "state": self._state, "sr": np.array(self.sample_rate, dtype=np.int64)},
                )
                speech = float(out[0][0]) >= self.config.threshold
                # `or` across windows: onset should be reported as soon as any
                # window in this frame is speech, rather than waiting for the
                # frame to be uniformly voiced.
                decided = speech if decided is None else (decided or speech)

            if decided is None:
                # This frame did not complete a window (320 < 512). Repeating the
                # previous verdict keeps the caller's run-length accounting
                # monotonic — returning False here would inject a phantom silent
                # frame mid-utterance and reset the speech run on every other
                # frame, so speech_ms could never accumulate.
                return self._last_speech
            self._last_speech = decided
            return decided
        except Exception:
            # One bad inference must not kill the session — but it must not be
            # invisible either. Degrading to the energy VAD is exactly how a
            # broken Silero contract stayed hidden: endpointing still "worked",
            # just badly, with nothing anywhere saying so. Warn once (not per
            # 20 ms frame, which would flood the log at 50 lines/second).
            if not self._warned_fallback:
                self._warned_fallback = True
                logger.warning("Silero VAD inference failed; falling back to energy VAD for this session", exc_info=True)
            return self._fallback._frame_is_speech(frame)


def frames_of(pcm: bytes, sample_rate: int = 16_000, frame_ms: int = FRAME_MS):
    """Split PCM16 into fixed-size frames, discarding any short trailing frame."""
    bytes_per_frame = int(sample_rate * frame_ms / 1000) * 2
    if bytes_per_frame <= 0:
        return
    for start in range(0, len(pcm) - bytes_per_frame + 1, bytes_per_frame):
        yield pcm[start : start + bytes_per_frame]
