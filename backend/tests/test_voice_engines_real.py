"""Real engines, real audio. No fakes anywhere in this file.

Every other voice test runs against the null engines so CI needs no weights.
These are the counterpart: they load the actual models and prove the pipeline
works on real sound. They **skip** (never fail) when the weights or the optional
dependencies are absent, so they stay green in CI while being a genuine gate on
a machine that has them.

    cd backend && uv sync --extra voice && ./scripts/fetch-voice-models.sh
    DEERFLOW_VOICE_MODEL_DIR=~/.cache/nova/voice uv run pytest tests/test_voice_engines_real.py -v

The centrepiece is the **round trip**: synthesize a known sentence with Kokoro,
feed that audio straight into faster-whisper, and check the words come back.
That single test exercises both engines and the PCM format contract between
them — a mismatch in sample rate, channel count, or endianness fails it, and
nothing else in the suite would catch that.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from deerflow.speech.base import STT_SAMPLE_RATE, AudioChunk

MODEL_DIR = Path(os.environ.get("DEERFLOW_VOICE_MODEL_DIR", Path.home() / ".cache/nova/voice"))
# The fetch script defaults to the int8 build (92 MB vs 325 MB, materially less
# resident memory). Accept either so the tests work whichever was downloaded.
KOKORO_MODEL = next(
    (MODEL_DIR / n for n in ("kokoro-v1.0.int8.onnx", "kokoro-v1.0.onnx") if (MODEL_DIR / n).is_file()),
    MODEL_DIR / "kokoro-v1.0.int8.onnx",
)
KOKORO_VOICES = MODEL_DIR / "voices-v1.0.bin"
SILERO_VAD = MODEL_DIR / "silero_vad.onnx"

pytest.importorskip("faster_whisper", reason="voice extra not installed (uv sync --extra voice)")


def _have(*paths: Path) -> bool:
    return all(p.is_file() and p.stat().st_size > 0 for p in paths)


requires_tts = pytest.mark.skipif(
    not _have(KOKORO_MODEL, KOKORO_VOICES),
    reason=f"Kokoro weights missing — run scripts/fetch-voice-models.sh (looked in {MODEL_DIR})",
)
requires_vad = pytest.mark.skipif(
    not _have(SILERO_VAD),
    reason=f"Silero VAD model missing — run scripts/fetch-voice-models.sh (looked in {MODEL_DIR})",
)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w}


def _rms(pcm: bytes) -> float:
    import numpy as np

    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm[: len(pcm) - (len(pcm) % 2)], dtype=np.int16)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


# Session-scoped so the (expensive) model load happens once for the whole file.
@pytest.fixture(scope="module")
def tts():
    from deerflow.speech.engines.kokoro_tts import KokoroTTS

    engine = KokoroTTS(model_path=str(KOKORO_MODEL), voices_path=str(KOKORO_VOICES))
    engine.warmup()
    yield engine
    engine.close()


@pytest.fixture(scope="module")
def stt():
    from deerflow.speech.engines.faster_whisper_stt import FasterWhisperSTT

    engine = FasterWhisperSTT(model="base", compute_type="int8", download_root=str(MODEL_DIR))
    engine.warmup()
    yield engine
    engine.close()


async def _synthesize(engine, text: str) -> AudioChunk:
    """Collect a full utterance from the streaming TTS."""
    pcm = bytearray()
    rate = engine.sample_rate
    async for chunk in engine.synthesize(text):
        pcm.extend(chunk.pcm)
        rate = chunk.sample_rate
    return AudioChunk(pcm=bytes(pcm), sample_rate=rate)


def _resample_to_16k(chunk: AudioChunk) -> AudioChunk:
    """Kokoro emits 24 kHz; Whisper wants 16 kHz."""
    import numpy as np

    if chunk.sample_rate == STT_SAMPLE_RATE:
        return chunk
    samples = np.frombuffer(chunk.pcm, dtype=np.int16)
    ratio = chunk.sample_rate / STT_SAMPLE_RATE
    idx = (np.arange(int(samples.size / ratio)) * ratio).astype(np.int64)
    idx = idx[idx < samples.size]
    return AudioChunk(pcm=samples[idx].tobytes(), sample_rate=STT_SAMPLE_RATE)


# ============================================================
# TTS — Kokoro
# ============================================================


@requires_tts
class TestRealTTS:
    def test_produces_audible_audio(self, tts, anyio_backend=None) -> None:
        import asyncio

        chunk = asyncio.run(_synthesize(tts, "Nova is online and ready."))
        assert chunk.sample_rate == 24_000
        assert len(chunk.pcm) % 2 == 0, "PCM16 must be a whole number of samples"
        assert chunk.duration_s > 0.5, f"suspiciously short: {chunk.duration_s:.2f}s"
        # Not silence — a model that loads but emits zeros would otherwise pass.
        assert _rms(chunk.pcm) > 200, f"output is effectively silent (rms={_rms(chunk.pcm):.1f})"

    def test_longer_text_produces_longer_audio(self, tts) -> None:
        import asyncio

        short = asyncio.run(_synthesize(tts, "Hello."))
        long = asyncio.run(_synthesize(tts, "Hello. This is a considerably longer sentence to speak aloud."))
        assert long.duration_s > short.duration_s

    def test_streams_incrementally_rather_than_one_blob(self, tts) -> None:
        """Streaming per sentence is what makes the first audio arrive fast."""
        import asyncio

        async def count():
            n = 0
            async for _ in tts.synthesize("First sentence here. Second sentence here. Third one too."):
                n += 1
            return n

        assert asyncio.run(count()) >= 3


# ============================================================
# STT — faster-whisper
# ============================================================


class TestRealSTT:
    def test_silence_transcribes_to_nothing(self, stt) -> None:
        """Silence must not invent words — the session uses this to skip a turn."""
        silence = AudioChunk(pcm=b"\x00\x00" * STT_SAMPLE_RATE, sample_rate=STT_SAMPLE_RATE)
        result = stt.transcribe(silence)
        assert not _words(result.text), f"heard {result.text!r} in pure silence"


# ============================================================
# The round trip — both engines, real audio, real format contract
# ============================================================


@requires_tts
class TestRoundTrip:
    @pytest.mark.parametrize(
        "sentence",
        [
            "The quick brown fox jumps over the lazy dog.",
            "Nova, deploy the application to production.",
        ],
    )
    def test_spoken_text_is_transcribed_back(self, tts, stt, sentence: str) -> None:
        """Speak it, hear it, and get the words back.

        This is the only test that proves the two engines agree on sample rate,
        channel count and endianness. Any mismatch there produces noise that
        transcribes to garbage, and no unit test would notice.
        """
        import asyncio

        spoken = asyncio.run(_synthesize(tts, sentence))
        heard = stt.transcribe(_resample_to_16k(spoken))

        expected = _words(sentence)
        got = _words(heard.text)
        overlap = expected & got
        # Not an exact match: TTS+STT is lossy and whisper punctuates its own
        # way. Most of the content words coming back is the real signal.
        assert len(overlap) >= max(2, int(len(expected) * 0.6)), f"round trip lost the sentence.\n  said:  {sentence!r}\n  heard: {heard.text!r}\n  overlap: {sorted(overlap)}"


# ============================================================
# VAD — Silero, on real speech
# ============================================================


@requires_vad
@requires_tts
class TestRealVad:
    def test_detects_real_speech_and_its_end(self, tts) -> None:
        import asyncio

        from deerflow.speech.vad import SileroVad, VadConfig, VadEvent, frames_of

        spoken = _resample_to_16k(asyncio.run(_synthesize(tts, "Hello Nova, can you hear me clearly?")))
        vad = SileroVad(VadConfig(speech_ms=100, silence_ms=300), sample_rate=STT_SAMPLE_RATE, model_path=str(SILERO_VAD))

        events = [vad.accept(f) for f in frames_of(spoken.pcm, STT_SAMPLE_RATE)]
        assert VadEvent.SPEECH_START in events, "real speech was not detected"

        # Trailing silence must end the utterance.
        silence_frame = b"\x00\x00" * (STT_SAMPLE_RATE * 20 // 1000)
        tail = [vad.accept(silence_frame) for _ in range(40)]
        assert VadEvent.SPEECH_END in events + tail, "the utterance never ended"


@requires_vad
class TestRealVadAlone:
    """VAD checks that need no TTS, so they run as soon as the 2 MB model lands."""

    def test_silence_alone_never_starts_an_utterance(self) -> None:
        from deerflow.speech.vad import SileroVad, VadConfig, VadEvent

        vad = SileroVad(VadConfig(speech_ms=100), sample_rate=STT_SAMPLE_RATE, model_path=str(SILERO_VAD))
        frame = b"\x00\x00" * (STT_SAMPLE_RATE * 20 // 1000)
        events = [vad.accept(frame) for _ in range(60)]
        assert VadEvent.SPEECH_START not in events

    def test_the_real_model_actually_loaded(self) -> None:
        """Guard the guard: SileroVad silently falls back to energy VAD when the
        model is missing, which would let every VAD test here pass without ever
        exercising the real thing."""
        from deerflow.speech.vad import SileroVad, VadConfig

        vad = SileroVad(VadConfig(), sample_rate=STT_SAMPLE_RATE, model_path=str(SILERO_VAD))
        vad.accept(b"\x00\x00" * (STT_SAMPLE_RATE * 20 // 1000))
        assert vad._session is not None, "fell back to EnergyVad — the ONNX model did not load"

    def test_real_vad_beats_a_loud_non_speech_burst(self) -> None:
        """The reason Silero is worth 2 MB over an RMS threshold: loud is not
        the same as speech, and an energy VAD cannot tell the difference."""
        import numpy as np

        from deerflow.speech.vad import EnergyVad, SileroVad, VadConfig, VadEvent

        rng = np.random.default_rng(1234)
        frame_samples = STT_SAMPLE_RATE * 20 // 1000
        noise = (rng.normal(0, 6000, frame_samples)).astype(np.int16).tobytes()

        silero = SileroVad(VadConfig(speech_ms=100), sample_rate=STT_SAMPLE_RATE, model_path=str(SILERO_VAD))
        energy = EnergyVad(VadConfig(speech_ms=100), sample_rate=STT_SAMPLE_RATE)

        silero_events = [silero.accept(noise) for _ in range(40)]
        energy_events = [energy.accept(noise) for _ in range(40)]

        assert VadEvent.SPEECH_START in energy_events, "fixture is not loud enough to fool an energy VAD"
        assert VadEvent.SPEECH_START not in silero_events, "Silero mistook white noise for speech"
