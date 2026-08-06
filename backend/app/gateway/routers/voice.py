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
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.gateway.ws_guards import caller_owns_thread, reject, ws_same_origin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# Client capture rate. Whisper wants 16 kHz; sending more is wasted bandwidth.
CLIENT_SAMPLE_RATE = 16_000
# Refuse to buffer an unbounded utterance even if the VAD never fires.
MAX_UTTERANCE_BYTES = CLIENT_SAMPLE_RATE * 2 * 60  # 60s of PCM16


@router.get("/status")
async def voice_status() -> dict:
    """Whether voice is usable, so the UI can hide the mic instead of failing on click."""
    from deerflow.speech.registry import is_speech_enabled

    enabled = is_speech_enabled()
    detail: dict = {"enabled": enabled, "sample_rate": CLIENT_SAMPLE_RATE}
    if enabled:
        try:
            from deerflow.speech.registry import get_stt_engine, get_tts_engine

            detail["stt"] = get_stt_engine().name
            detail["tts"] = get_tts_engine().name
            detail["ready"] = True
        except Exception as e:
            # Configured but not installed: say so plainly instead of 500ing.
            detail["ready"] = False
            detail["reason"] = str(e)
    return detail


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
        respond: Callable[[str], AsyncIterator[str]],
        voice: str | None = None,
    ) -> None:
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._stt = stt
        self._tts = tts
        self._vad = vad
        self._respond = respond
        self._voice = voice

        self._utterance = bytearray()
        self._capturing = False
        self._speak_task: asyncio.Task | None = None
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
                self._capturing = True
                self._utterance.clear()
                await self._send_json({"type": "listening"})

            if self._capturing:
                self._utterance.extend(frame)
                if len(self._utterance) > MAX_UTTERANCE_BYTES:
                    # Hard ceiling; treat as end-of-turn rather than growing.
                    event = VadEvent.SPEECH_END

            if event is VadEvent.SPEECH_END and self._capturing:
                self._capturing = False
                audio = bytes(self._utterance)
                self._utterance.clear()
                await self._handle_utterance(audio)

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
        self.turns.append(text)
        await self._send_json({"type": "thinking"})
        self._speak_task = asyncio.create_task(self._respond_and_speak(text))

    async def _respond_and_speak(self, text: str) -> None:
        try:
            await self._send_json({"type": "speaking", "sample_rate": getattr(self._tts, "sample_rate", 24_000)})
            async for reply in self._respond(text):
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

    def _drain(queue: queue_mod.Queue[str | None], text: str) -> None:
        from deerflow.client import DeerFlowClient

        client = DeerFlowClient()
        buffer = ""
        seen: dict[str, str] = {}
        try:
            for event in client.stream(text, thread_id=thread_id):
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
            if tail:
                queue.put(tail)
        except Exception as e:
            logger.warning("voice agent run failed: %s", e)
        finally:
            queue.put(None)  # sentinel: generation finished

    async def respond(text: str) -> AsyncIterator[str]:
        # client.stream is a *sync* generator, so it runs on a worker thread and
        # hands sentences back through a queue. Doing it inline would block the
        # event loop and stall every other socket on this worker.
        queue: queue_mod.Queue[str | None] = queue_mod.Queue()
        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(None, _drain, queue, text)
        try:
            while True:
                item = await loop.run_in_executor(None, queue.get)
                if item is None:
                    break
                yield item
        finally:
            # Cancellation (barge-in) leaves the worker running to completion;
            # it is bounded by the agent run and its output is simply dropped.
            with contextlib.suppress(Exception):
                await task

    return respond


@router.websocket("/session/{thread_id}")
async def voice_session(websocket: WebSocket, thread_id: str) -> None:
    """Full-duplex voice for one thread."""
    if not caller_owns_thread(thread_id):
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

    from deerflow.speech.vad import SileroVad, VadConfig

    await websocket.accept()
    session = VoiceSession(
        send_json=websocket.send_json,
        send_bytes=websocket.send_bytes,
        stt=stt,
        tts=tts,
        vad=SileroVad(VadConfig.from_env(), sample_rate=CLIENT_SAMPLE_RATE),
        respond=await _agent_responder(thread_id),
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
