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


class TestSileroInputContract:
    """Pin the tensor shape Nova hands Silero — no weights, so this runs in CI.

    Silero's ONNX graph has dynamic axes: a wrong-shaped input returns a
    meaningless probability instead of raising. Nova shipped a VAD that fed it
    320-sample frames with no context prefix, and on real speech the model never
    exceeded p=0.06 — completely deaf, while every weightless test stayed green
    because silence and noise still (correctly) produced no speech.

    Measured against the real model: 512-sample window + 64-sample context
    reaches p=1.00 on speech; drop the context and the same audio caps at 0.06.
    These tests assert the shape, which is the part CI can check.
    """

    @staticmethod
    def _vad_with_stub(monkeypatch, widths: list[int], prob: float = 0.9):
        np = pytest.importorskip("numpy")
        from deerflow.speech import vad as vad_mod

        class StubSession:
            def run(self, _outputs, feeds):
                widths.append(int(feeds["input"].shape[-1]))
                # onnxruntime returns a flat list of the graph's outputs —
                # here [probability, stateN]. Returning ([prob], state) instead
                # would shift every index in the caller by one and silently
                # route it into the energy-VAD fallback.
                return [np.array([[prob]], dtype=np.float32), feeds["state"]]

        vad = vad_mod.SileroVad(VadConfig(speech_ms=40, silence_ms=200), sample_rate=16_000)
        vad._session = StubSession()
        vad._load_attempted = True
        vad._state = np.zeros((2, 1, 128), dtype=np.float32)
        return vad

    def test_model_receives_window_plus_context(self, monkeypatch) -> None:
        widths: list[int] = []
        vad = self._vad_with_stub(monkeypatch, widths)
        for _ in range(20):
            vad.accept(LOUD)
        assert widths, "the model was never invoked"
        # 512 window + 64 context. Not 512, and never the raw 320-sample frame.
        assert set(widths) == {576}, f"wrong input width(s): {sorted(set(widths))}"

    def test_frames_are_buffered_not_dropped(self) -> None:
        """20 ms frames are 320 samples and windows are 512 — they do not divide
        evenly, so leftover audio must carry over rather than be discarded."""
        widths: list[int] = []
        vad = self._vad_with_stub(None, widths)
        for _ in range(32):  # 32 * 320 = 10240 samples = exactly 20 windows
            vad.accept(LOUD)
        assert len(widths) == 20, f"expected 20 windows from 10240 samples, got {len(widths)}"

    def test_a_partial_window_holds_the_previous_verdict(self) -> None:
        """A frame too short to complete a window must not report silence —
        that would reset the speech run every other frame and stop speech_ms
        from ever accumulating."""
        widths: list[int] = []
        vad = self._vad_with_stub(None, widths, prob=0.99)
        vad.accept(LOUD)  # 320 samples: no window completes yet
        vad.accept(LOUD)  # 640: one window, verdict True
        before = len(widths)
        assert vad._frame_is_speech(LOUD) is True  # 960: still no new window
        assert len(widths) == before, "no new inference should have run"

    def test_unsupported_sample_rate_falls_back_instead_of_guessing(self) -> None:
        from deerflow.speech.vad import SileroVad

        vad = SileroVad(VadConfig(), sample_rate=44_100, model_path=__file__)
        vad._ensure_session()
        assert vad._session is None, "a mis-windowed Silero is worse than an honest energy VAD"

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
        async def respond(text, cancel):
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

        async def respond(text, cancel):
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

        async def respond(text, cancel):
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
        async def respond(text, cancel):
            yield "ok"

        session = _session(ScriptedSTT(["hi"]), ToneTTS(), respond)
        _speak(session, [LOUD] * 4 + [QUIET] * 8)
        # One completed turn, no barge-in.
        assert _socket.kinds().count("interrupt") == 0

    def test_a_failing_turn_reports_an_error_instead_of_dying(self) -> None:
        async def respond(text, cancel):
            raise RuntimeError("model exploded")
            yield  # pragma: no cover

        session = _session(ScriptedSTT(["hi"]), ToneTTS(), respond)
        _speak(session, [LOUD] * 4 + [QUIET] * 8)
        assert "error" in _socket.kinds()

    def test_typed_text_still_gets_spoken(self) -> None:
        """Accessibility: type instead of speak, Nova still answers aloud."""

        async def respond(text, cancel):
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


class TestLongTaskCancellation:
    """Interrupting a long task must stop the *agent run*, not just playback.

    Cancelling only the asyncio task would leave the worker thread streaming
    tokens into a void — on a long task that is real money and real latency for
    output nobody will ever hear.
    """

    def test_barge_in_signals_the_run_to_stop(self) -> None:
        observed: dict[str, object] = {}
        produced: list[str] = []

        async def respond(text, cancel):
            observed["cancel"] = cancel
            # A long task: many sentences, yielding between each.
            for i in range(200):
                if cancel.is_set():
                    return
                produced.append(f"s{i}")
                yield f"Sentence number {i}."
                await asyncio.sleep(0.005)

        session = _session(ScriptedSTT(["start the long job", "stop"]), ToneTTS(chunk_ms=20, chunk_delay_s=0.01), respond)

        async def go():
            for f in [LOUD] * 4 + [QUIET] * 8:
                await session.on_audio(f)
            await asyncio.sleep(0.05)
            assert session.is_speaking, "the long turn never started"
            # Talk over it.
            for f in [LOUD] * 4:
                await session.on_audio(f)

        asyncio.run(go())

        cancel = observed.get("cancel")
        assert cancel is not None, "the responder was never given a cancel token"
        assert cancel.is_set(), "barge-in did not signal the agent run to stop"
        # And it genuinely stopped early rather than producing all 200
        # sentences. (The generator is usually closed at its await point before
        # the flag check runs — either path is a real stop; what must never
        # happen is the task running to completion.)
        assert len(produced) < 200, f"the long task ran to completion ({len(produced)} sentences)"

    def test_each_turn_gets_a_fresh_cancel_token(self) -> None:
        """A previous barge-in must not kill the turn the user just started."""
        tokens = []

        async def respond(text, cancel):
            tokens.append(cancel)
            yield "ok"

        session = _session(ScriptedSTT(["one"]), ToneTTS(), respond)

        async def go():
            await session.handle_text("first")
            for _ in range(50):
                await asyncio.sleep(0)
                if not session.is_speaking:
                    break
            await session.close()  # sets the first token
            await session.handle_text("second")
            for _ in range(50):
                await asyncio.sleep(0)
                if not session.is_speaking:
                    break

        asyncio.run(go())
        assert len(tokens) == 2
        assert tokens[0] is not tokens[1]
        assert not tokens[1].is_set(), "the new turn inherited a cancelled token"

    def test_explicit_stop_also_halts_the_run(self) -> None:
        """The stop control must work mid-task, not only between turns."""
        seen: dict[str, object] = {}

        async def respond(text, cancel):
            seen["cancel"] = cancel
            for i in range(200):
                if cancel.is_set():
                    return
                yield f"Part {i}."
                await asyncio.sleep(0.005)

        session = _session(ScriptedSTT(["go"]), ToneTTS(chunk_ms=20, chunk_delay_s=0.01), respond)

        async def go():
            await session.handle_text("run something long")
            await asyncio.sleep(0.05)
            assert session.is_speaking
            await session.close()

        asyncio.run(go())
        assert seen["cancel"].is_set()  # type: ignore[union-attr]
        assert not session.is_speaking


class TestStatusProbeHonesty:
    """`/api/voice/status` must not claim voice works when it doesn't.

    Engines construct lazily — they don't touch model weights until first use —
    so "the object constructed" is no evidence at all. An earlier version
    reported ready on that basis and offered a mic that failed the moment it was
    clicked. The probe now warms the engines, which is what actually fails when
    weights or dependencies are missing.
    """

    def _status(self, monkeypatch, *, stt=None, tts=None, enabled=True):
        import asyncio

        from app.gateway.routers import voice as voice_mod
        from deerflow.speech import registry

        monkeypatch.setattr(registry, "is_speech_enabled", lambda: enabled)
        monkeypatch.setattr(voice_mod, "_readiness", None)
        if stt is not None:
            monkeypatch.setattr(registry, "get_stt_engine", lambda: stt)
        if tts is not None:
            monkeypatch.setattr(registry, "get_tts_engine", lambda: tts)
        return asyncio.run(voice_mod.voice_status())

    def test_reports_not_ready_when_warmup_fails(self, monkeypatch) -> None:
        class _Broken(ToneTTS):
            def warmup(self) -> None:
                raise SpeechEngineUnavailable("Voices file not found at /nope.bin")

        out = self._status(monkeypatch, stt=ScriptedSTT(), tts=_Broken())
        assert out["enabled"] is True
        assert out["ready"] is False
        assert "Voices file not found" in out["reason"]

    def test_reports_ready_when_engines_warm_up(self, monkeypatch) -> None:
        out = self._status(monkeypatch, stt=ScriptedSTT(), tts=ToneTTS())
        assert out["ready"] is True
        assert out["stt"] == "scripted"
        assert out["tts"] == "tone"
        assert "reason" not in out

    def test_disabled_short_circuits_without_touching_engines(self, monkeypatch) -> None:
        """Voice off must not pay the model-load cost."""

        def _boom():
            raise AssertionError("engines must not be built when voice is disabled")

        from deerflow.speech import registry

        monkeypatch.setattr(registry, "get_tts_engine", _boom)
        out = self._status(monkeypatch, enabled=False)
        assert out["enabled"] is False
        assert "ready" not in out


class TestOneShotSpeak:
    """`POST /api/voice/speak` — Nova talking without a conversation.

    The login greeting is why this exists. Opening the duplex socket to say
    hello would prompt for the microphone before the user has asked for
    anything, which is the reliable way to get that permission denied forever.
    """

    def _speak(self, monkeypatch, body, *, tts=None, enabled=True):
        import asyncio

        from app.gateway.routers import voice as voice_mod
        from deerflow.speech import registry

        monkeypatch.setattr(registry, "is_speech_enabled", lambda: enabled)
        if tts is not None:
            monkeypatch.setattr(registry, "get_tts_engine", lambda: tts)

        class _Req:
            async def json(self):
                return body

        # The route function is wrapped by @require_auth; call the underlying
        # implementation so this stays a unit test of the synthesis path.
        fn = getattr(voice_mod.voice_speak, "__wrapped__", voice_mod.voice_speak)
        return asyncio.run(fn(_Req()))

    def test_endpoint_is_auth_guarded(self) -> None:
        """The tests above unwrap `@require_auth` to reach the synthesis path,
        so nothing else here would notice if the decorator were removed. Without
        it this is an unauthenticated, CPU-burning endpoint on a public origin.
        """
        from app.gateway.routers import voice as voice_mod

        assert hasattr(voice_mod.voice_speak, "__wrapped__"), "@require_auth is missing from /api/voice/speak"

    def test_returns_a_playable_wav(self, monkeypatch) -> None:
        resp = self._speak(monkeypatch, {"text": "Good evening. Nova here."}, tts=ToneTTS())
        assert resp.media_type == "audio/wav"
        body = resp.body
        assert body[:4] == b"RIFF" and body[8:12] == b"WAVE", "not a WAV container"
        # The declared payload size must match the bytes actually present, or
        # decodeAudioData rejects the whole buffer in the browser.
        import struct

        declared = struct.unpack("<I", body[40:44])[0]
        assert declared == len(body) - 44
        assert declared > 0, "a WAV with no samples plays as silence"

    def test_wav_header_declares_the_engine_sample_rate(self, monkeypatch) -> None:
        """A wrong rate in the header does not fail — it just plays chipmunked."""
        import struct

        tts = ToneTTS()
        resp = self._speak(monkeypatch, {"text": "hello"}, tts=tts)
        assert struct.unpack("<I", resp.body[24:28])[0] == tts.sample_rate

    def test_rejects_empty_text(self, monkeypatch) -> None:
        from fastapi import HTTPException

        for body in ({"text": "   "}, {"text": ""}, {}):
            with pytest.raises(HTTPException) as e:
                self._speak(monkeypatch, body, tts=ToneTTS())
            assert e.value.status_code == 400

    def test_caps_length_so_it_cannot_become_a_synthesis_service(self, monkeypatch) -> None:
        from app.gateway.routers.voice import MAX_SPEAK_CHARS

        spoken: list[str] = []

        class _Recording(ToneTTS):
            async def synthesize(self, text, *, voice=None):
                spoken.append(text)
                async for chunk in super().synthesize(text, voice=voice):
                    yield chunk

        self._speak(monkeypatch, {"text": "a" * (MAX_SPEAK_CHARS * 3)}, tts=_Recording())
        assert len(spoken[0]) == MAX_SPEAK_CHARS

    def test_503_when_voice_is_disabled(self, monkeypatch) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as e:
            self._speak(monkeypatch, {"text": "hi"}, enabled=False)
        assert e.value.status_code == 503

    def test_engine_failure_is_503_not_500(self, monkeypatch) -> None:
        from fastapi import HTTPException

        class _Broken(ToneTTS):
            def warmup(self) -> None:
                raise SpeechEngineUnavailable("weights missing")

        with pytest.raises(HTTPException) as e:
            self._speak(monkeypatch, {"text": "hi"}, tts=_Broken())
        assert e.value.status_code == 503
