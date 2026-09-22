"""Voice transport: full-duplex speech over one WebSocket.

Design notes worth knowing before editing
-----------------------------------------
**Raw PCM, not WebM/Opus.** The browser captures with an AudioWorklet and sends
16 kHz mono PCM16 frames. No container means no ffmpeg in the realtime path and
noticeably lower latency; ffmpeg is only needed to decode *uploaded* files.

**Barge-in is why this is a WebSocket and not two HTTP calls.** While Nova is
speaking we keep listening. The moment the VAD reports speech, we cancel the
synthesis task and tell the client to flush its playback queue. You cannot
retract audio already handed to the sound device, so the client keeps an
explicit queue of scheduled buffers it can drop — which is also why playback is
``AudioBufferSourceNode``s rather than an ``<audio>`` element.

**This is a transport, not a second agent runtime.** Transcripts are handed to
the existing run path and assistant text comes back the same way it does for
typed chat.

Wire protocol (one socket, mixed frame types)
--------------------------------------------
client -> server: binary PCM16 frames, plus JSON control
    {"type": "start"}                 begin a session
    {"type": "stop"}                  end it
    {"type": "config", "voice": ...}  set the TTS voice
    {"type": "text", "text": ...}     type instead of speak (accessibility)

server -> client: JSON events, plus binary PCM16 audio
    {"type": "ready", "sample_rate": 16000}
    {"type": "listening"}
    {"type": "transcript", "text": ..., "final": true}
    {"type": "thinking"}
    {"type": "assistant", "text": ...}
    {"type": "speaking", "sample_rate": 24000}
    {"type": "interrupt"}             stop playback NOW, drop buffered audio
    {"type": "idle"}
    {"type": "error", "message": ...}
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue as queue_mod
import re
import threading
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from app.gateway.authz import require_auth
from app.gateway.ws_guards import reject, ws_caller_owns_thread, ws_same_origin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# Client capture rate. Whisper wants 16 kHz; sending more is wasted bandwidth.
CLIENT_SAMPLE_RATE = 16_000
# Refuse to buffer an unbounded utterance even if the VAD never fires.
MAX_UTTERANCE_BYTES = CLIENT_SAMPLE_RATE * 2 * 60  # 60s of PCM16

# A responder turns a transcript into a stream of speakable text. It takes a
# cancel event so a long-running turn can actually be stopped mid-generation.
Responder = Callable[[str, threading.Event], AsyncIterator[str]]


# Cached readiness probe. Constructing an engine is lazy — it does not touch
# model weights — so "it constructed" is NOT evidence that voice works. We have
# to actually warm it up, which is expensive, so the outcome is cached.
_readiness: dict | None = None


def _probe_engines() -> dict:
    """Actually load the engines and report honestly whether voice will work.

    An earlier version reported ready purely because the engine objects
    constructed. They construct lazily, so it said yes with model paths that did
    not exist — the UI offered a mic that failed the moment you clicked it.
    """
    from deerflow.speech.registry import get_stt_engine, get_tts_engine

    detail: dict = {}
    try:
        stt = get_stt_engine()
        tts = get_tts_engine()
        detail["stt"] = stt.name
        detail["tts"] = tts.name
        # The part that matters: this raises when weights or deps are missing.
        stt.warmup()
        tts.warmup()
        detail["ready"] = True
    except Exception as e:
        detail["ready"] = False
        detail["reason"] = str(e)
    return detail


@router.get("/status")
async def voice_status(refresh: bool = False) -> dict:
    """Whether voice is genuinely usable, so the UI can tell the truth.

    Warming the engines can take seconds on a cold start, so it runs off the
    event loop and the result is cached. ``?refresh=1`` re-probes after an
    operator installs weights, without a gateway restart.
    """
    global _readiness
    from deerflow.speech.registry import is_speech_enabled

    enabled = is_speech_enabled()
    detail: dict = {"enabled": enabled, "sample_rate": CLIENT_SAMPLE_RATE}
    if not enabled:
        return detail

    if _readiness is None or refresh:
        _readiness = await asyncio.to_thread(_probe_engines)
    return {**detail, **_readiness}


# Engines the settings panel offers. Kept here rather than discovered by import
# scanning: every entry is a class path the registry will `resolve_class`, and a
# typo should fail at config-write time with a clear message, not at first use.
ENGINE_CATALOG: dict[str, list[dict]] = {
    "stt": [
        {
            "use": "deerflow.speech.engines.faster_whisper_stt:FasterWhisperSTT",
            "label": "Whisper (faster-whisper)",
            "languages": "99 languages, including Urdu, Hindi and Arabic, and it handles code-switching mid-sentence.",
            "models": ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo", "distil-large-v3"],
            "default": True,
        },
    ],
    "turn": [
        {
            "use": "deerflow.speech.turn:SmartTurnV3",
            "label": "Smart Turn v3",
            "note": (
                "Judges whether you finished a *thought*, not just whether sound stopped — so Nova waits "
                "while you pause mid-sentence instead of interrupting. 8 MB, ~12 ms on CPU. Needs "
                "smart-turn-v3.2-cpu.onnx from scripts/fetch-voice-models.sh."
            ),
        },
    ],
    "tts": [
        {
            "use": "deerflow.speech.engines.kokoro_tts:KokoroTTS",
            "label": "Kokoro-82M",
            "note": "Apache-2.0. Use the fp32 weights — the int8 build is ~5.5x slower on the same hardware.",
            "voices": ["af_heart", "af_bella", "af_nicole", "af_sarah", "am_adam", "am_michael", "bf_emma", "bm_george"],
            "default": True,
        },
        {
            "use": "deerflow.speech.engines.null:ToneTTS",
            "label": "Test tone (no weights)",
            "note": "Synthesizes a tone instead of speech. For checking the audio path when weights are absent.",
        },
    ],
}


def _engine_status(engine, kind: str) -> dict:
    """What an engine actually is at runtime, not what was requested."""
    return {
        "name": getattr(engine, "name", kind),
        "device_requested": getattr(engine, "device", None),
        "device_actual": getattr(engine, "resolved_device", None),
        "model": getattr(engine, "model_size", None) or getattr(engine, "_model_path", None),
        "compute_type": getattr(engine, "compute_type", None),
        "sample_rate": getattr(engine, "sample_rate", None),
    }


def _config_payload() -> dict:
    """The one shape every /config verb returns.

    GET, PUT and DELETE all answer with this. They used to differ — PUT replied
    `{saved, settings}` with no catalog — and the panel does `setConfig(response)`
    after a save, so changing any setting wiped the catalog and the next render
    crashed on `config.catalog.stt`. Endpoints that describe the same resource
    should describe it the same way; anything else pushes the difference onto
    every caller.
    """
    from deerflow.speech import registry

    settings = registry._merged_config()
    payload: dict = {
        "settings": settings,
        "catalog": ENGINE_CATALOG,
        "overrides_path": str(registry.overrides_path()),
        "has_overrides": registry.overrides_path().is_file(),
    }
    # Report what is loaded *now* — device_requested vs device_actual is the
    # whole point, since `auto` silently resolves and `cuda` can fall back.
    if settings.get("enabled"):
        with contextlib.suppress(Exception):
            payload["live"] = {
                "stt": _engine_status(registry.get_stt_engine(), "stt"),
                "tts": _engine_status(registry.get_tts_engine(), "tts"),
            }
    return payload


@router.get("/config")
@require_auth
async def get_voice_config(request: Request) -> dict:
    """Current voice settings, the catalog to choose from, and live state."""
    return _config_payload()


@router.put("/config")
@require_auth
async def put_voice_config(request: Request) -> dict:
    """Write voice settings and make them take effect immediately.

    Writes to a **separate overrides file**, never `config.yaml`: that file is
    operator-owned and commented, and `yaml.dump` would strip every comment.
    """
    global _readiness
    from deerflow.speech import registry

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")

    # Validate engine class paths before persisting, so a typo cannot leave
    # voice unloadable until someone reads the logs.
    for section in ("stt", "tts"):
        cfg = body.get(section)
        if isinstance(cfg, dict) and cfg.get("use"):
            from deerflow.reflection import resolve_class
            from deerflow.speech.base import SpeechToText, TextToSpeech

            base = SpeechToText if section == "stt" else TextToSpeech
            try:
                resolve_class(cfg["use"], base)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"speech.{section}.use is not usable: {e}") from e

    await asyncio.to_thread(registry.save_overrides, body)
    # The cached readiness answer describes engines that no longer exist.
    _readiness = None
    return _config_payload()


@router.delete("/config")
@require_auth
async def reset_voice_config(request: Request) -> dict:
    """Drop the overrides and fall back to `config.yaml`."""
    global _readiness
    from deerflow.speech import registry

    await asyncio.to_thread(registry.clear_overrides)
    _readiness = None
    return _config_payload()


# Long enough for a greeting or a short confirmation, short enough that this
# cannot be used as an open-ended synthesis service. Synthesis is CPU-bound and
# runs in the gateway process, so an unbounded body here is a cheap way to eat
# every core.
MAX_SPEAK_CHARS = 400


def _wav_of(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM16 mono in a WAV header.

    The socket path streams bare PCM because the client already knows the
    format. A one-shot reply has no such context, and a WAV means the browser
    can hand it straight to ``decodeAudioData`` with no custom parsing.
    """
    import struct

    return b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm


# A diagnostic clip, not a transcription service. Long enough to say a sentence.
MAX_TRANSCRIBE_SECONDS = 30
MAX_TRANSCRIBE_BYTES = CLIENT_SAMPLE_RATE * 2 * MAX_TRANSCRIBE_SECONDS + 1024


def _pcm_from_wav(data: bytes) -> tuple[bytes, int]:
    """Extract PCM16 mono and its rate from a WAV, without ffmpeg.

    The client records raw PCM through the same AudioWorklet the live session
    uses and wraps it in a WAV header, so a full decoder is unnecessary — and
    ffmpeg is absent from the running gateway image, which would make a
    container-format upload fail exactly where the diagnostic is most needed.
    """
    import io
    import wave

    with wave.open(io.BytesIO(data), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise HTTPException(status_code=400, detail="expected 16-bit PCM audio")
        channels = wav.getnchannels()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if channels > 1:
        # Keep the first channel rather than mixing: capture is mono by
        # construction, so more than one channel means something is misconfigured
        # and averaging would hide it.
        import array

        samples = array.array("h", frames)
        frames = array.array("h", samples[::channels]).tobytes()
    return frames, rate


@router.post("/transcribe")
@require_auth
async def voice_transcribe(request: Request) -> dict:
    """Transcribe a short uploaded clip. Backs the settings panel's mic test.

    This is the one check that covers the whole capture path — permission,
    device, sample rate, worklet, and the STT engine — in a single action.
    """
    from deerflow.speech.base import AudioChunk
    from deerflow.speech.registry import get_stt_engine, is_speech_enabled

    if not is_speech_enabled():
        raise HTTPException(status_code=503, detail="Voice is not enabled")

    form = await request.form()
    upload = form.get("audio")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="an 'audio' file part is required")

    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="the audio part was empty")
    if len(data) > MAX_TRANSCRIBE_BYTES:
        raise HTTPException(status_code=413, detail=f"clip exceeds {MAX_TRANSCRIBE_SECONDS}s")

    try:
        pcm, rate = _pcm_from_wav(data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"could not read the audio (expected WAV PCM16): {e}") from e

    def _run() -> str:
        engine = get_stt_engine()
        engine.warmup()
        return (engine.transcribe(AudioChunk(pcm=pcm, sample_rate=rate)).text or "").strip()

    try:
        text = await asyncio.to_thread(_run)
    except Exception as e:
        logger.warning("voice /transcribe failed: %s", e)
        raise HTTPException(status_code=503, detail="Transcription unavailable") from e

    return {"text": text, "sample_rate": rate, "duration_s": round(len(pcm) / 2 / rate, 2) if rate else None}


@router.post("/speak")
@require_auth
async def voice_speak(request: Request) -> Response:
    """Synthesize a short piece of text and return it as a WAV.

    This exists for Nova speaking *without* a conversation — the login greeting
    is the motivating case. Opening the full duplex socket for that would ask
    for microphone permission just to say hello, which is both rude and a
    reliable way to get the permission denied for good.
    """
    from deerflow.speech.registry import get_tts_engine, is_speech_enabled

    if not is_speech_enabled():
        raise HTTPException(status_code=503, detail="Voice is not enabled")

    body = await request.json()
    text = (body or {}).get("text") or ""
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    text = text.strip()[:MAX_SPEAK_CHARS]
    voice = (body or {}).get("voice") or None

    def _render() -> tuple[bytes, int]:
        engine = get_tts_engine()
        engine.warmup()
        pcm = bytearray()
        rate = engine.sample_rate

        async def _drain() -> None:
            nonlocal rate
            async for chunk in engine.synthesize(text, voice=voice):
                pcm.extend(chunk.pcm)
                rate = chunk.sample_rate

        asyncio.run(_drain())
        return bytes(pcm), rate

    try:
        # Synthesis is CPU-bound; keep it off the event loop or it stalls every
        # other request on this worker for the duration.
        pcm, rate = await asyncio.to_thread(_render)
    except Exception as e:
        logger.warning("voice /speak failed: %s", e)
        raise HTTPException(status_code=503, detail="Speech synthesis unavailable") from e

    return Response(
        content=_wav_of(pcm, rate),
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


class VoiceSession:
    """One conversation over one socket.

    Kept separate from the route handler so the turn-taking logic can be unit
    tested against fake engines and a fake socket, with no server involved.
    """

    def __init__(
        self,
        send_json: Callable[[dict], asyncio.Future | None],
        send_bytes: Callable[[bytes], asyncio.Future | None],
        *,
        stt,
        tts,
        vad,
        respond: Responder,
        voice: str | None = None,
        turn_detector=None,
        turn_config=None,
    ) -> None:
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._stt = stt
        self._tts = tts
        self._vad = vad
        self._respond = respond
        self._voice = voice

        # Semantic endpointing. Absent, it degrades to exactly the VAD-only
        # behaviour, so turn detection can never be the reason voice breaks.
        from deerflow.speech.turn import AlwaysComplete, TurnConfig

        self._turn = turn_detector or AlwaysComplete()
        self._turn_config = turn_config or TurnConfig()
        self._extensions = 0

        self._utterance = bytearray()
        self._capturing = False
        self._speak_task: asyncio.Task | None = None
        # Set to stop the *agent run* (not just playback). A long task must
        # actually halt when interrupted, otherwise it keeps generating into a
        # void — the reason cancellation is cooperative rather than a bare
        # task.cancel() is that client.stream is a sync generator on a worker
        # thread, which asyncio cannot interrupt.
        self._run_cancel = threading.Event()
        self.turns: list[str] = []

    @property
    def is_speaking(self) -> bool:
        return self._speak_task is not None and not self._speak_task.done()

    async def on_audio(self, pcm: bytes) -> None:
        """Feed captured audio. Drives VAD, capture buffering and barge-in."""
        from deerflow.speech.vad import VadEvent, frames_of

        for frame in frames_of(pcm, CLIENT_SAMPLE_RATE):
            event = self._vad.accept(frame)

            if event is VadEvent.SPEECH_START:
                # Barge-in: the user talked over Nova.
                if self.is_speaking:
                    await self._cancel_speech()
                    await self._send_json({"type": "interrupt"})

                # Only a *new* utterance resets state. When `_capturing` is
                # already true we are mid-turn — the semantic detector judged the
                # thought unfinished and we kept listening, so this is the user
                # resuming, not starting over. Clearing here would discard the
                # first half of the sentence ("...maybe we should" + "deploy on
                # Friday" arriving as just the second half), and resetting the
                # counter would defeat the extension cap entirely.
                if not self._capturing:
                    self._capturing = True
                    self._utterance.clear()
                    self._extensions = 0
                await self._send_json({"type": "listening"})

            forced = False
            if self._capturing:
                self._utterance.extend(frame)
                if len(self._utterance) > MAX_UTTERANCE_BYTES:
                    # Hard ceiling; treat as end-of-turn rather than growing.
                    # `forced` skips the semantic check — at this point we are
                    # out of buffer, so "is the thought finished?" is moot.
                    event = VadEvent.SPEECH_END
                    forced = True

            if event is VadEvent.SPEECH_END and self._capturing:
                audio = bytes(self._utterance)
                if not forced and not await self._turn_has_ended(audio):
                    # The speaker paused mid-thought. Keep the buffer and keep
                    # listening rather than answering an unfinished sentence —
                    # this is the difference between a conversation and an
                    # interrogation.
                    self._extensions += 1
                    self._vad.reset()
                    continue

                self._capturing = False
                self._extensions = 0
                self._utterance.clear()
                await self._handle_utterance(audio)

    async def _turn_has_ended(self, pcm: bytes) -> bool:
        """Has the speaker finished a thought, not merely stopped making noise?

        Bounded by `max_extensions` so a detector that keeps saying "not yet"
        cannot hold the turn open indefinitely — a wrong *incomplete* verdict
        costs the user a real wait, which is worse than answering slightly early.
        """
        if self._extensions >= self._turn_config.max_extensions:
            return True
        try:
            probability = await asyncio.to_thread(self._turn.completion_probability, pcm, CLIENT_SAMPLE_RATE)
        except Exception:
            logger.debug("turn detection failed; ending the turn", exc_info=True)
            return True
        ended = probability >= self._turn_config.threshold
        if not ended:
            await self._send_json({"type": "listening", "reason": "incomplete"})
        return ended

    async def _handle_utterance(self, pcm: bytes) -> None:
        from deerflow.speech.base import AudioChunk

        if not pcm:
            return
        transcript = await asyncio.to_thread(self._stt.transcribe, AudioChunk(pcm=pcm, sample_rate=CLIENT_SAMPLE_RATE))
        text = (transcript.text or "").strip()
        if not text:
            # Silence or noise. Say nothing rather than starting an empty run.
            await self._send_json({"type": "idle"})
            return
        await self._send_json({"type": "transcript", "text": text, "final": True})
        await self.handle_text(text)

    async def handle_text(self, text: str) -> None:
        """Run one turn: agent response, then speak it."""
        # Fresh cancel token per turn — a previous barge-in must not kill the
        # turn the user just started.
        self._run_cancel = threading.Event()
        self.turns.append(text)
        await self._send_json({"type": "thinking"})
        self._speak_task = asyncio.create_task(self._respond_and_speak(text))

    async def _respond_and_speak(self, text: str) -> None:
        try:
            await self._send_json({"type": "speaking", "sample_rate": getattr(self._tts, "sample_rate", 24_000)})
            async for reply in self._respond(text, self._run_cancel):
                if not reply:
                    continue
                await self._send_json({"type": "assistant", "text": reply})
                async for chunk in self._tts.synthesize(reply, voice=self._voice):
                    await self._send_bytes(chunk.pcm)
            await self._send_json({"type": "idle"})
        except asyncio.CancelledError:
            # Barge-in. Expected; the interrupt frame is sent by the canceller
            # so it reaches the client even if this task dies first.
            raise
        except Exception as e:
            logger.warning("voice turn failed: %s", e)
            with contextlib.suppress(Exception):
                await self._send_json({"type": "error", "message": "voice turn failed"})

    async def _cancel_speech(self) -> None:
        # Stop generation first, then playback. Order matters: cancelling the
        # asyncio task alone would leave the worker thread streaming tokens.
        self._run_cancel.set()
        task = self._speak_task
        self._speak_task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def close(self) -> None:
        await self._cancel_speech()


# Speak once a sentence is complete rather than waiting for the whole reply.
# Waiting is what makes an assistant feel laggy: Nova would sit silent through
# the entire generation and then talk. Punctuation is the natural boundary
# because it is also where the TTS wants to break for prosody.
_SENTENCE_END = re.compile(r"[.!?]['\")\]]?\s")


async def _agent_responder(thread_id: str) -> Callable[[str], AsyncIterator[str]]:
    """Bridge a transcript to the normal agent run path, streaming sentences out.

    Deliberately thin: voice must not become a second place where agent
    behaviour is defined. The existing client streams AI text as *deltas*
    keyed by message id, so we accumulate and cut on sentence boundaries.
    """

    def _drain(queue: queue_mod.Queue[str | None], text: str, cancel: threading.Event) -> None:
        from deerflow.client import DeerFlowClient

        client = DeerFlowClient()
        buffer = ""
        seen: dict[str, str] = {}
        stream = client.stream(text, thread_id=thread_id)
        try:
            for event in stream:
                # Cooperative cancellation. Without this the agent keeps
                # generating after a barge-in — on a long task that burns
                # tokens producing output nobody will ever hear. Closing the
                # generator in `finally` unwinds the graph for real.
                if cancel.is_set():
                    break
                if event.type != "messages-tuple":
                    continue
                data = event.data or {}
                if data.get("type") != "ai":
                    continue
                delta = str(data.get("content") or "")
                if not delta:
                    continue
                # Deltas are per message id; a new id means a new message.
                msg_id = str(data.get("id") or "")
                if msg_id and seen.get(msg_id) is None:
                    seen[msg_id] = ""
                buffer += delta

                # Emit every complete sentence sitting in the buffer.
                while (m := _SENTENCE_END.search(buffer)) is not None:
                    cut = m.end()
                    sentence = buffer[:cut].strip()
                    buffer = buffer[cut:]
                    if sentence:
                        queue.put(sentence)
            tail = buffer.strip()
            if tail and not cancel.is_set():
                queue.put(tail)
        except Exception as e:
            logger.warning("voice agent run failed: %s", e)
        finally:
            with contextlib.suppress(Exception):
                stream.close()  # GeneratorExit unwinds the graph run
            queue.put(None)  # sentinel: generation finished

    async def respond(text: str, cancel: threading.Event) -> AsyncIterator[str]:
        # client.stream is a *sync* generator, so it runs on a worker thread and
        # hands sentences back through a queue. Doing it inline would block the
        # event loop and stall every other socket on this worker.
        queue: queue_mod.Queue[str | None] = queue_mod.Queue()
        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(None, _drain, queue, text, cancel)
        try:
            while True:
                item = await loop.run_in_executor(None, queue.get)
                if item is None:
                    break
                yield item
        finally:
            # Tell the worker to stop, then wait for it. Setting the flag is
            # what makes a barge-in during a long task actually stop the run
            # rather than orphan it.
            cancel.set()
            with contextlib.suppress(Exception):
                await task

    return respond


@router.websocket("/session/{thread_id}")
async def voice_session(websocket: WebSocket, thread_id: str) -> None:
    """Full-duplex voice for one thread."""
    # The auth middleware never runs for WebSocket scope (BaseHTTPMiddleware is
    # skipped), so authenticate from the session cookie and stamp the contextvar
    # before any ownership check — otherwise caller_owns_thread resolves to
    # DEFAULT_USER_ID and every real user is rejected.
    from app.gateway.ws_guards import ws_user
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    user = await ws_user(websocket)
    if user is None:
        await reject(websocket)
        return
    token = set_current_user(user)
    try:
        await _voice_session_after_auth(websocket, thread_id)
    finally:
        reset_current_user(token)


async def _voice_session_after_auth(websocket: WebSocket, thread_id: str) -> None:
    """The session loop, run with the authenticated user's context set."""
    if not await ws_caller_owns_thread(websocket, thread_id):
        await reject(websocket)
        return
    if not ws_same_origin(websocket):
        await reject(websocket)
        return

    from deerflow.speech.registry import get_stt_engine, get_tts_engine, is_speech_enabled

    if not is_speech_enabled():
        await reject(websocket, code=1011)
        return

    try:
        stt = get_stt_engine()
        tts = get_tts_engine()
    except Exception as e:
        logger.warning("voice session refused, engines unavailable: %s", e)
        await reject(websocket, code=1011)
        return

    from deerflow.speech.registry import _merged_config
    from deerflow.speech.turn import TurnConfig, get_turn_detector
    from deerflow.speech.vad import SileroVad, VadConfig

    # Semantic endpointing is opt-in and degrades to VAD-only when off or when
    # its weights are missing, so it can never be the reason a session fails.
    turn_settings = dict(_merged_config().get("turn") or {})

    await websocket.accept()
    session = VoiceSession(
        send_json=websocket.send_json,
        send_bytes=websocket.send_bytes,
        stt=stt,
        tts=tts,
        vad=SileroVad(VadConfig.from_env(), sample_rate=CLIENT_SAMPLE_RATE),
        respond=await _agent_responder(thread_id),
        turn_detector=get_turn_detector(turn_settings),
        turn_config=TurnConfig.from_env(),
    )

    try:
        await websocket.send_json({"type": "ready", "sample_rate": CLIENT_SAMPLE_RATE})
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            # Binary is audio; text is control. Never receive_text() here — it
            # raises on binary frames, which is most of this stream.
            if (pcm := message.get("bytes")) is not None:
                await session.on_audio(pcm)
            elif (raw := message.get("text")) is not None:
                await _handle_control(session, raw)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("voice session error for thread %s: %s", thread_id.replace("\n", ""), e)
    finally:
        await session.close()
        with contextlib.suppress(Exception):
            await websocket.close()


async def _handle_control(session: VoiceSession, raw: str) -> None:
    import json

    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return
    kind = msg.get("type")
    if kind == "stop":
        await session.close()
    elif kind == "config" and msg.get("voice"):
        session._voice = str(msg["voice"])
    elif kind == "text" and msg.get("text"):
        # Typed input through the voice channel — Nova still answers aloud.
        await session.handle_text(str(msg["text"]))
