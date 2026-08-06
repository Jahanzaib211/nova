"""Voice: turn-taking, barge-in, and the engine registry.

Everything here runs against the dependency-free engines in
``deerflow.speech.engines.null`` — no model weights, no onnxruntime, no network.
That is deliberate: a voice suite that skips when weights are absent stops
protecting anything, and weights are exactly what CI will never have.
"""

from __future__ import annotations

import asyncio

import pytest

from deerflow.speech.base import AudioChunk, SpeechEngineUnavailable, SpeechToText, TextToSpeech, Transcript
from deerflow.speech.engines.kokoro_tts import split_for_speech
from deerflow.speech.engines.null import ScriptedSTT, SilentSTT, ToneTTS
from deerflow.speech.vad import EnergyVad, VadConfig, VadEvent, frames_of

FRAME_BYTES = 16_000 * 2 * 20 // 1000  # 20 ms of 16 kHz PCM16

LOUD = b"\x00\x40" * (FRAME_BYTES // 2)
QUIET = b"\x00\x00" * (FRAME_BYTES // 2)


# ============================================================
# VAD — endpointing is what makes barge-in possible
# ============================================================


class TestVad:
    def test_sustained_speech_starts_an_utterance(self) -> None:
        vad = EnergyVad(VadConfig(speech_ms=40, silence_ms=200))
        events = [vad.accept(LOUD) for _ in range(4)]
        assert VadEvent.SPEECH_START in events
        assert vad.is_speaking

    def test_a_single_loud_frame_is_not_speech(self) -> None:
        """A cough or a door slam must not interrupt Nova."""
        vad = EnergyVad(VadConfig(speech_ms=160, silence_ms=200))
        assert vad.accept(LOUD) is VadEvent.NONE
        assert not vad.is_speaking

    def test_sustained_silence_ends_the_utterance(self) -> None:
        vad = EnergyVad(VadConfig(speech_ms=40, silence_ms=100))
        for _ in range(4):
            vad.accept(LOUD)
        events = [vad.accept(QUIET) for _ in range(8)]
        assert VadEvent.SPEECH_END in events
        assert not vad.is_speaking

    def test_brief_pause_does_not_end_the_utterance(self) -> None:
        """People pause mid-sentence; cutting them off there is the classic bug."""
        vad = EnergyVad(VadConfig(speech_ms=40, silence_ms=600))
        for _ in range(4):
            vad.accept(LOUD)
        events = [vad.accept(QUIET) for _ in range(5)]  # 100ms
        assert VadEvent.SPEECH_END not in events
        assert vad.is_speaking

    def test_a_stuck_mic_cannot_buffer_forever(self) -> None:
        vad = EnergyVad(VadConfig(speech_ms=40, silence_ms=10_000, max_utterance_ms=200))
        events = [vad.accept(LOUD) for _ in range(40)]
        assert VadEvent.SPEECH_END in events

    def test_reset_clears_state(self) -> None:
        vad = EnergyVad(VadConfig(speech_ms=40))
        for _ in range(4):
            vad.accept(LOUD)
        vad.reset()
        assert not vad.is_speaking

    def test_frames_of_splits_exactly_and_drops_a_short_tail(self) -> None:
        pcm = b"\x01\x02" * (FRAME_BYTES // 2 * 3 + 5)
        frames = list(frames_of(pcm))
        assert len(frames) == 3
        assert all(len(f) == FRAME_BYTES for f in frames)


# ============================================================
# Engine contracts
# ============================================================


class TestEngines:
    def test_scripted_stt_returns_its_script_in_order(self) -> None:
        stt = ScriptedSTT(["first", "second"])
        chunk = AudioChunk(b"\x00\x00", 16_000)
        assert stt.transcribe(chunk).text == "first"
        assert stt.transcribe(chunk).text == "second"
        assert stt.transcribe(chunk).text == "second"  # repeats the last

    def test_empty_transcript_is_falsy(self) -> None:
        """The session uses truthiness to decide whether to start a run."""
        assert not Transcript(text="   ")
        assert Transcript(text="hi")

    def test_tone_tts_emits_real_pcm_at_its_declared_rate(self) -> None:
        async def go():
            tts = ToneTTS(sample_rate=24_000, chunk_ms=100)
            chunks = [c async for c in tts.synthesize("hello")]
            assert chunks
            for c in chunks:
                assert c.sample_rate == 24_000
                assert len(c.pcm) % 2 == 0  # whole PCM16 samples
            return chunks

        chunks = asyncio.run(go())
        assert sum(c.duration_s for c in chunks) > 0

    def test_audio_chunk_duration(self) -> None:
        assert AudioChunk(b"\x00\x00" * 16_000, 16_000).duration_s == pytest.approx(1.0)
        assert AudioChunk(b"", 0).duration_s == 0.0

    def test_split_for_speech_keeps_sentences_whole(self) -> None:
        assert split_for_speech("One. Two! Three?") == ["One.", "Two!", "Three?"]

    def test_split_for_speech_wraps_a_runaway_sentence(self) -> None:
        """Streaming per sentence is what makes the first audio arrive fast."""
        long = " ".join(["word"] * 200)
        pieces = split_for_speech(long, max_chars=100)
        assert len(pieces) > 1
        assert all(len(p) <= 100 for p in pieces)

    def test_split_for_speech_handles_empty(self) -> None:
        assert split_for_speech("") == []


# ============================================================
# VoiceSession — turn-taking and barge-in
# ============================================================


class _FakeSocket:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.audio: list[bytes] = []

    async def send_json(self, payload: dict) -> None:
        self.events.append(payload)

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)

    def kinds(self) -> list[str]:
        return [e.get("type") for e in self.events]


def _session(stt: SpeechToText, tts: TextToSpeech, respond, *, vad=None):
    from app.gateway.routers.voice import VoiceSession

    return VoiceSession(
        send_json=_socket.send_json,
        send_bytes=_socket.send_bytes,
        stt=stt,
        tts=tts,
        vad=vad or EnergyVad(VadConfig(speech_ms=40, silence_ms=100)),
        respond=respond,
    )


_socket: _FakeSocket


@pytest.fixture(autouse=True)
def _fresh_socket():
    global _socket
    _socket = _FakeSocket()
    return _socket


def _speak(session, frames: list[bytes]):
    async def go():
        for f in frames:
            await session.on_audio(f)
        # Let the response task finish.
        for _ in range(50):
            await asyncio.sleep(0)
            if not session.is_speaking:
                break

    asyncio.run(go())


class TestVoiceSession:
    def test_a_full_turn_transcribes_responds_and_speaks(self) -> None:
        async def respond(text):
            yield f"you said {text}"

        session = _session(ScriptedSTT(["hello nova"]), ToneTTS(), respond)
        _speak(session, [LOUD] * 4 + [QUIET] * 8)

        kinds = _socket.kinds()
        assert "listening" in kinds
        assert "transcript" in kinds
        assert "thinking" in kinds
        assert "speaking" in kinds
        assert session.turns == ["hello nova"]
        assert _socket.audio, "no audio was streamed back"

    def test_silence_does_not_start_a_run(self) -> None:
        """Noise that transcribes to nothing must not spawn an agent turn."""
        called = []

        async def respond(text):
            called.append(text)
            yield "should not happen"

        session = _session(SilentSTT(), ToneTTS(), respond)
        _speak(session, [LOUD] * 4 + [QUIET] * 8)

        assert called == []
        assert "thinking" not in _socket.kinds()
        assert "idle" in _socket.kinds()

    def test_speaking_while_nova_talks_interrupts_it(self) -> None:
        """Barge-in: the whole reason this is a duplex socket."""
        started = asyncio.Event()

        async def respond(text):
            started.set()
            yield "a very long reply that would take a while to speak aloud"

        # A delay per chunk so the reply is genuinely in-flight when we talk over it.
        session = _session(ScriptedSTT(["hi", "stop"]), ToneTTS(chunk_ms=20, chunk_delay_s=0.01), respond)

        async def go():
            for f in [LOUD] * 4 + [QUIET] * 8:
                await session.on_audio(f)
            await asyncio.sleep(0.02)  # let synthesis get under way
            assert session.is_speaking
            # Talk over it.
            for f in [LOUD] * 4:
                await session.on_audio(f)
            assert not session.is_speaking, "synthesis was not cancelled"

        asyncio.run(go())
        assert "interrupt" in _socket.kinds()

    def test_interrupt_is_not_sent_when_nova_is_silent(self) -> None:
        async def respond(text):
            yield "ok"

        session = _session(ScriptedSTT(["hi"]), ToneTTS(), respond)
        _speak(session, [LOUD] * 4 + [QUIET] * 8)
        # One completed turn, no barge-in.
        assert _socket.kinds().count("interrupt") == 0

    def test_a_failing_turn_reports_an_error_instead_of_dying(self) -> None:
        async def respond(text):
            raise RuntimeError("model exploded")
            yield  # pragma: no cover

        session = _session(ScriptedSTT(["hi"]), ToneTTS(), respond)
        _speak(session, [LOUD] * 4 + [QUIET] * 8)
        assert "error" in _socket.kinds()

    def test_typed_text_still_gets_spoken(self) -> None:
        """Accessibility: type instead of speak, Nova still answers aloud."""

        async def respond(text):
            yield f"echo {text}"

        session = _session(ScriptedSTT(), ToneTTS(), respond)

        async def go():
            await session.handle_text("typed question")
            for _ in range(50):
                await asyncio.sleep(0)
                if not session.is_speaking:
                    break

        asyncio.run(go())
        assert session.turns == ["typed question"]
        assert _socket.audio


# ============================================================
# Registry
# ============================================================


class TestRegistry:
    def test_voice_is_off_unless_enabled(self, monkeypatch) -> None:
        """Absent config means off, not broken."""
        from deerflow.speech import registry

        monkeypatch.setattr(registry, "_speech_config", lambda: {})
        assert registry.is_speech_enabled() is False

    def test_engines_are_built_from_config(self, monkeypatch) -> None:
        from deerflow.speech import registry

        monkeypatch.setattr(
            registry,
            "_speech_config",
            lambda: {
                "enabled": True,
                "stt": {"use": "deerflow.speech.engines.null:ScriptedSTT"},
                "tts": {"use": "deerflow.speech.engines.null:ToneTTS"},
            },
        )
        registry.reset_engines()
        try:
            assert registry.is_speech_enabled() is True
            assert registry.get_stt_engine().name == "scripted"
            assert registry.get_tts_engine().name == "tone"
        finally:
            registry.reset_engines()

    def test_engines_are_singletons(self, monkeypatch) -> None:
        """Weights are tens to hundreds of MB — never per-request."""
        from deerflow.speech import registry

        monkeypatch.setattr(
            registry,
            "_speech_config",
            lambda: {"enabled": True, "stt": {"use": "deerflow.speech.engines.null:ScriptedSTT"}},
        )
        registry.reset_engines()
        try:
            assert registry.get_stt_engine() is registry.get_stt_engine()
        finally:
            registry.reset_engines()

    def test_a_bad_config_key_names_the_section(self, monkeypatch) -> None:
        from deerflow.speech import registry

        monkeypatch.setattr(
            registry,
            "_speech_config",
            lambda: {"enabled": True, "stt": {"use": "deerflow.speech.engines.null:ScriptedSTT", "nonsense": 1}},
        )
        registry.reset_engines()
        try:
            with pytest.raises(SpeechEngineUnavailable) as exc:
                registry.get_stt_engine()
            assert "speech.stt" in str(exc.value)
        finally:
            registry.reset_engines()

    def test_set_engines_injects_directly(self) -> None:
        from deerflow.speech import registry

        stt = ScriptedSTT(["injected"])
        registry.set_engines(stt=stt)
        try:
            assert registry.get_stt_engine() is stt
        finally:
            registry.reset_engines()


class TestSentenceStreaming:
    """Nova must start speaking on the first finished sentence, not at the end.

    Waiting for the whole reply is what makes an assistant feel laggy — several
    seconds of silence, then a monologue.
    """

    def test_sentence_boundaries_are_detected(self) -> None:
        from app.gateway.routers.voice import _SENTENCE_END

        buffer = "Hello there. How are you? Fine! Done"
        cuts = []
        while (m := _SENTENCE_END.search(buffer)) is not None:
            cuts.append(buffer[: m.end()].strip())
            buffer = buffer[m.end() :]
        assert cuts == ["Hello there.", "How are you?", "Fine!"]
        assert buffer.strip() == "Done"  # tail flushed separately

    def test_a_decimal_does_not_split_a_sentence(self) -> None:
        """`3.5` must not be mistaken for a sentence end."""
        from app.gateway.routers.voice import _SENTENCE_END

        assert _SENTENCE_END.search("the value is 3.5 exactly") is None

    def test_quoted_and_bracketed_endings_are_handled(self) -> None:
        from app.gateway.routers.voice import _SENTENCE_END

        for text in ['He said "go." Then left', "See note (a.) Next"]:
            assert _SENTENCE_END.search(text) is not None
