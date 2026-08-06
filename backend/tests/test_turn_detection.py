"""Semantic turn detection.

Two layers, deliberately:

* **Hermetic** — the input contract, the fallback behaviour, and the session's
  turn-extension logic. These need no weights and run in CI.
* **Real model** — skipped without weights. Includes an *oracle* comparison
  against `transformers`' `WhisperFeatureExtractor`, which is the reference
  implementation the model was trained with. Nova computes the mel features with
  `faster_whisper` instead, to keep the voice stack on a single runtime with no
  torch; that substitution has to be proven equivalent rather than assumed.

Why the oracle test exists: the model card describes the input as "raw
waveform", but the exported graph takes an ``[1, 80, 800]`` mel spectrogram. Its
axes are dynamic, so a wrong-shaped or wrong-scaled input returns a plausible
number instead of raising — exactly how the Silero VAD ended up deaf.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from deerflow.speech.turn import (
    MEL_BINS,
    MEL_FRAMES,
    WINDOW_SAMPLES,
    AlwaysComplete,
    SmartTurnV3,
    TurnConfig,
    get_turn_detector,
)

MODEL_DIR = Path(os.environ.get("DEERFLOW_VOICE_MODEL_DIR", Path.home() / ".cache/nova/voice"))
TURN_MODEL = MODEL_DIR / "smart-turn-v3.2-cpu.onnx"

requires_model = pytest.mark.skipif(
    not (TURN_MODEL.is_file() and TURN_MODEL.stat().st_size > 0),
    reason=f"smart-turn weights missing — run scripts/fetch-voice-models.sh (looked in {MODEL_DIR})",
)


class TestFallbackIsAlwaysSafe:
    """Turn detection is an enhancement. It must never be why voice breaks."""

    def test_disabled_config_gives_vad_only_behaviour(self) -> None:
        assert isinstance(get_turn_detector({}), AlwaysComplete)
        assert isinstance(get_turn_detector({"enabled": False}), AlwaysComplete)

    def test_always_complete_ends_every_turn(self) -> None:
        assert AlwaysComplete().completion_probability(b"\x00\x00" * 1000) == 1.0
        assert AlwaysComplete().is_complete(b"") is True

    def test_missing_weights_degrade_to_ending_the_turn(self) -> None:
        """Not the reverse: a detector that fails *closed* would hold every turn
        open and make Nova appear to have stopped responding."""
        detector = SmartTurnV3(model_path="/nope/does-not-exist.onnx")
        assert detector.completion_probability(b"\x00\x00" * 16_000) == 1.0

    def test_warmup_says_what_is_missing(self) -> None:
        from deerflow.speech.base import SpeechEngineUnavailable

        with pytest.raises(SpeechEngineUnavailable, match="fetch-voice-models"):
            SmartTurnV3(model_path="/nope/does-not-exist.onnx").warmup()


class TestSessionTurnLogic:
    """The part that decides whether Nova waits or answers."""

    @staticmethod
    def _session(detector, config=None):
        from app.gateway.routers.voice import VoiceSession
        from deerflow.speech.engines.null import ScriptedSTT, ToneTTS
        from deerflow.speech.vad import EnergyVad, VadConfig

        sent: list[dict] = []

        async def send_json(msg):
            sent.append(msg)

        async def send_bytes(_):
            pass

        async def respond(_text, _cancel):
            yield "ok"

        session = VoiceSession(
            send_json,
            send_bytes,
            stt=ScriptedSTT(["hello"]),
            tts=ToneTTS(),
            vad=EnergyVad(VadConfig(speech_ms=40, silence_ms=100)),
            respond=respond,
            turn_detector=detector,
            turn_config=config or TurnConfig(),
        )
        return session, sent

    @staticmethod
    def _speak_then_pause(session):
        """Drive one utterance followed by enough silence to fire SPEECH_END."""
        loud = b"\x00\x40" * 320
        quiet = b"\x00\x00" * 320
        asyncio.run(session.on_audio(loud * 6))
        asyncio.run(session.on_audio(quiet * 12))

    def test_a_complete_thought_is_answered(self) -> None:
        session, _ = self._session(AlwaysComplete())
        self._speak_then_pause(session)
        assert session.turns, "a finished utterance was never handled"

    def test_an_incomplete_thought_keeps_listening(self) -> None:
        """The whole point: a mid-sentence pause must not end the turn."""

        class NeverDone(AlwaysComplete):
            def completion_probability(self, pcm, sample_rate=16_000):
                return 0.0

        session, sent = self._session(NeverDone())
        self._speak_then_pause(session)
        assert not session.turns, "Nova answered an unfinished sentence"
        assert any(m.get("reason") == "incomplete" for m in sent), "the UI was not told why it is still listening"

    def test_patience_is_bounded(self) -> None:
        """A detector stuck on 'not yet' must not hold the turn open forever."""

        class NeverDone(AlwaysComplete):
            def completion_probability(self, pcm, sample_rate=16_000):
                return 0.0

        session, _ = self._session(NeverDone(), TurnConfig(max_extensions=2))
        for _ in range(5):
            self._speak_then_pause(session)
        assert session.turns, "the turn never ended despite the extension cap"

    def test_resuming_after_a_pause_keeps_the_first_half(self) -> None:
        """The bug this caught: `SPEECH_START` cleared the buffer, so a sentence
        split by a thinking pause arrived as only its second half.

        "…maybe we should" [pause] "deploy on Friday" must reach the STT engine
        as one utterance, not as "deploy on Friday".
        """
        seen: list[int] = []

        class DoneOnSecondPause(AlwaysComplete):
            def __init__(self):
                self.calls = 0

            def completion_probability(self, pcm, sample_rate=16_000):
                self.calls += 1
                seen.append(len(pcm))
                return 0.0 if self.calls == 1 else 1.0

        session, _ = self._session(DoneOnSecondPause())
        self._speak_then_pause(session)  # first half, judged unfinished
        self._speak_then_pause(session)  # user resumes, then really stops

        assert len(seen) >= 2, "the detector was not consulted twice"
        assert seen[1] > seen[0], f"audio was discarded on resume: {seen[0]} bytes then {seen[1]}"

    def test_a_detector_that_raises_ends_the_turn(self) -> None:
        class Broken(AlwaysComplete):
            def completion_probability(self, pcm, sample_rate=16_000):
                raise RuntimeError("boom")

        session, _ = self._session(Broken())
        self._speak_then_pause(session)
        assert session.turns, "a broken detector silently swallowed the turn"


@requires_model
class TestRealModel:
    @pytest.fixture(scope="class")
    def detector(self):
        d = SmartTurnV3(model_path=str(TURN_MODEL))
        d.warmup()
        return d

    def test_the_real_model_actually_loaded(self, detector) -> None:
        """Guard the guard: every method above falls back to 1.0 when the model
        is missing, which would let these tests pass without it."""
        assert detector._session is not None

    def test_input_contract_is_what_we_assume(self, detector) -> None:
        shape = detector._session.get_inputs()[0].shape
        assert shape[1] == MEL_BINS and shape[2] == MEL_FRAMES, f"model input changed: {shape}"

    def test_returns_a_probability(self, detector) -> None:
        import numpy as np

        pcm = (np.zeros(WINDOW_SAMPLES, dtype=np.int16)).tobytes()
        p = detector.completion_probability(pcm)
        assert 0.0 <= p <= 1.0

    def test_short_audio_is_padded_not_rejected(self, detector) -> None:
        """Real utterances are usually shorter than the model's 8s window."""
        import numpy as np

        pcm = (np.zeros(1600, dtype=np.int16)).tobytes()  # 0.1s
        assert 0.0 <= detector.completion_probability(pcm) <= 1.0

    def test_matches_the_reference_feature_extractor(self, detector) -> None:
        """Nova computes mel features with faster_whisper; the model was trained
        with transformers' WhisperFeatureExtractor. Prove they agree.

        Verified equal to within 0.02 probability on speech at the time of
        writing. If this ever fails, Nova's features have drifted from what the
        model expects and turn detection is quietly degraded.
        """
        transformers = pytest.importorskip("transformers", reason="oracle only; not a runtime dependency")
        import numpy as np

        rng = np.random.default_rng(7)
        audio = (rng.normal(0, 0.05, WINDOW_SAMPLES)).astype(np.float32)

        ref = transformers.WhisperFeatureExtractor(chunk_length=8)
        feats = ref(audio, sampling_rate=16_000, padding="max_length", max_length=WINDOW_SAMPLES, truncation=True, do_normalize=True, return_tensors="np")
        reference_input = np.asarray(feats.input_features, dtype=np.float32)[:, :, :MEL_FRAMES]
        name = detector._session.get_inputs()[0].name
        reference_p = float(detector._session.run(None, {name: reference_input})[0][0][0])

        ours = detector.completion_probability((audio * 32767).astype(np.int16).tobytes())
        assert abs(ours - reference_p) < 0.02, f"feature pipeline drifted from the reference: ours={ours:.3f} reference={reference_p:.3f}"
