"""Semantic turn detection: has the speaker finished a *thought*?

A VAD only knows whether sound stopped. That makes Nova cut you off when you
pause mid-sentence to think, and it is the single biggest reason a voice
assistant feels robotic. Smart Turn is a Whisper-Tiny encoder with a linear head
that judges completion from **prosody** — pitch contour, final lengthening, the
shape of the trailing silence — so "I was thinking that maybe we should…" reads
as unfinished even though the audio went quiet.

Input contract (verified against the ONNX graph, not assumed)
-------------------------------------------------------------
The model card says it "analyses the raw waveform", which is true of *their
library* but not of the exported graph. The graph takes::

    input_features: [batch, 80, 800]   float32
    logits:         [batch, 1]         float32, already sigmoid'd

That is an 80-bin log-mel spectrogram over the **last 8 seconds** (800 frames at
a 10 ms hop). Feeding a waveform would run without raising and return a
meaningless number — the same failure mode that left the Silero VAD deaf. So the
shape is asserted here rather than trusted.

The reference pipeline (pipecat's ``inference.py``) is:

1. keep the last 8 s of audio
2. right-pad to exactly 8 s
3. normalise the waveform to zero mean / unit variance
4. Whisper log-mel, 80 bins, ``chunk_length=8``

Step 4 uses ``faster_whisper``'s extractor rather than ``transformers``' so the
voice stack keeps its single-runtime, no-torch shape. That substitution is
**validated**, not hoped for: ``tests/test_turn_detection.py`` compares both
against the real model and skips when ``transformers`` is absent.
"""

from __future__ import annotations

import abc
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
#: The model's window. Not configurable — it is baked into the exported graph.
WINDOW_SECONDS = 8
WINDOW_SAMPLES = SAMPLE_RATE * WINDOW_SECONDS
MEL_BINS = 80
MEL_FRAMES = 800


@dataclass
class TurnConfig:
    #: Above this, the utterance is treated as a finished thought.
    threshold: float = 0.7
    #: How much longer to keep listening when the detector says "not yet".
    #: Kept modest: a wrong "incomplete" costs the user a real wait.
    extend_ms: int = 700
    #: Cap on consecutive extensions, so a confused detector cannot hold the
    #: turn open forever. With the default that is ~2.1s of extra patience.
    max_extensions: int = 3

    @classmethod
    def from_env(cls) -> TurnConfig:
        def _f(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, default))
            except (TypeError, ValueError):
                return default

        return cls(
            threshold=_f("DEERFLOW_TURN_THRESHOLD", 0.7),
            extend_ms=int(_f("DEERFLOW_TURN_EXTEND_MS", 700)),
            max_extensions=int(_f("DEERFLOW_TURN_MAX_EXTENSIONS", 3)),
        )


class TurnDetector(abc.ABC):
    """Given the utterance so far, how likely is it that the turn has ended?"""

    name = "turn"

    @abc.abstractmethod
    def completion_probability(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
        """Return a probability in [0, 1]."""

    def is_complete(self, pcm: bytes, sample_rate: int = SAMPLE_RATE, *, threshold: float = 0.7) -> bool:
        return self.completion_probability(pcm, sample_rate) >= threshold

    def close(self) -> None:  # pragma: no cover - trivial
        pass


class AlwaysComplete(TurnDetector):
    """The no-op detector: every pause ends the turn.

    This is exactly today's VAD-only behaviour, so it is both the default when
    the model is missing and the fixture that lets the whole suite run without
    weights.
    """

    name = "always-complete"

    def completion_probability(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
        return 1.0


class SmartTurnV3(TurnDetector):
    """pipecat-ai/smart-turn-v3 via onnxruntime. ~8 MB, ~12 ms on CPU.

    Runs on CPU by default and deliberately so: it is small enough that the GPU
    buys nothing, and leaving it on CPU keeps VRAM for the STT/TTS models.
    """

    name = "smart-turn-v3"

    def __init__(self, model_path: str | None = None, device: str = "cpu") -> None:
        self._model_path = model_path or os.environ.get("DEERFLOW_TURN_MODEL_PATH")
        self.device = device
        self._session = None
        self._features = None
        self._load_attempted = False

    def _ensure(self) -> bool:
        if self._load_attempted:
            return self._session is not None
        self._load_attempted = True
        if not self._model_path or not os.path.exists(self._model_path):
            return False
        try:
            import onnxruntime as ort

            from deerflow.speech.devices import resolve_onnx_providers

            providers, _ = resolve_onnx_providers(self.device, subsystem="smart-turn")
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            session = ort.InferenceSession(self._model_path, sess_options=opts, providers=providers)

            # Assert the contract instead of trusting it. Dynamic axes mean a
            # wrong-shaped input returns a plausible number rather than raising,
            # so a silently mismatched model would degrade turn-taking with
            # nothing anywhere saying why.
            shape = session.get_inputs()[0].shape
            if len(shape) != 3 or shape[1] != MEL_BINS or shape[2] != MEL_FRAMES:
                raise ValueError(f"expected an [batch, {MEL_BINS}, {MEL_FRAMES}] mel input, got {shape}")

            from faster_whisper.feature_extractor import FeatureExtractor

            self._features = FeatureExtractor(feature_size=MEL_BINS, chunk_length=WINDOW_SECONDS)
            self._session = session
        except Exception:
            logger.warning("smart-turn model could not be loaded; turn detection falls back to VAD-only", exc_info=True)
            self._session = None
        return self._session is not None

    def warmup(self) -> None:
        if not self._ensure():
            from deerflow.speech.base import SpeechEngineUnavailable

            raise SpeechEngineUnavailable(f"smart-turn weights not found at {self._model_path!r}. Run scripts/fetch-voice-models.sh")

    def completion_probability(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
        if not self._ensure():
            # No model: behave exactly like the VAD-only pipeline rather than
            # holding the turn open on every pause.
            return 1.0
        try:
            import numpy as np

            samples = np.frombuffer(pcm[: len(pcm) - (len(pcm) % 2)], dtype=np.int16)
            if samples.size == 0:
                return 1.0
            audio = samples.astype(np.float32) / 32768.0
            if sample_rate != SAMPLE_RATE:
                # Nearest-neighbour is adequate here: the mel front end is far
                # coarser than the aliasing this introduces, and capture is
                # already 16 kHz in every path that reaches this.
                ratio = sample_rate / SAMPLE_RATE
                idx = (np.arange(int(audio.size / ratio)) * ratio).astype(np.int64)
                audio = audio[idx[idx < audio.size]]

            # Keep the END of the utterance — that is where the prosodic cue for
            # completion lives — then right-pad, matching the reference.
            audio = audio[-WINDOW_SAMPLES:]
            if audio.size < WINDOW_SAMPLES:
                audio = np.pad(audio, (0, WINDOW_SAMPLES - audio.size))
            audio = ((audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)).astype(np.float32)

            feats = np.asarray(self._features(audio), dtype=np.float32)[:, :MEL_FRAMES][None, ...]
            out = self._session.run(None, {self._session.get_inputs()[0].name: feats})
            return float(np.clip(out[0][0][0], 0.0, 1.0))
        except Exception:
            logger.debug("smart-turn inference failed; treating the turn as complete", exc_info=True)
            return 1.0


def get_turn_detector(config: dict | None = None) -> TurnDetector:
    """Build the configured detector, falling back to VAD-only behaviour.

    Turn detection is an *enhancement*: if anything about it is missing or
    misconfigured, voice must still work exactly as it does today.
    """
    cfg = dict(config or {})
    if not cfg.get("enabled", False):
        return AlwaysComplete()
    detector = SmartTurnV3(model_path=cfg.get("model_path"), device=cfg.get("device", "cpu"))
    return detector
