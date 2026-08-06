"""Dependency-free engines used by tests and as a safe default.

Every voice test in this repo runs against these, so CI needs no model weights,
no onnxruntime, and no network. That is deliberate: a test suite that silently
skips when weights are absent is a test suite that stops protecting you.
"""

from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import AsyncIterator

from deerflow.speech.base import AudioChunk, SpeechToText, TextToSpeech, Transcript


class ScriptedSTT(SpeechToText):
    """Returns pre-set transcripts in order, then repeats the last one.

    Lets a test drive a whole conversation deterministically without audio.
    """

    name = "scripted"

    def __init__(self, transcripts: list[str] | None = None) -> None:
        self._transcripts = list(transcripts or ["hello nova"])
        self._index = 0
        self.calls: list[AudioChunk] = []

    def transcribe(self, audio: AudioChunk) -> Transcript:
        self.calls.append(audio)
        text = self._transcripts[min(self._index, len(self._transcripts) - 1)]
        self._index += 1
        return Transcript(text=text, language="en", is_final=True)


class SilentSTT(SpeechToText):
    """Hears nothing. Used to assert that empty transcripts don't start a run."""

    name = "silent"

    def transcribe(self, audio: AudioChunk) -> Transcript:
        return Transcript(text="", is_final=True)


class ToneTTS(TextToSpeech):
    """Emits a sine tone proportional to the text length.

    Real audio bytes at the right sample rate and frame size, so the transport,
    the chunking and the client playback queue are all genuinely exercised —
    just without shipping a neural vocoder into CI.
    """

    name = "tone"

    def __init__(self, sample_rate: int = 24_000, chunk_ms: int = 100, freq: float = 220.0, chunk_delay_s: float = 0.0) -> None:
        self.sample_rate = sample_rate
        self.chunk_ms = chunk_ms
        self.freq = freq
        # Real engines await between chunks (inference is offloaded to a
        # thread). Yielding here too keeps this fake cancellable at the same
        # points, which is what barge-in depends on — a generator with no await
        # points cannot be interrupted mid-reply at all.
        self.chunk_delay_s = chunk_delay_s
        self.spoken: list[str] = []

    async def synthesize(self, text: str, *, voice: str | None = None) -> AsyncIterator[AudioChunk]:
        self.spoken.append(text)
        # ~60ms of audio per character, bounded so a long reply stays testable.
        total_ms = min(max(len(text) * 60, self.chunk_ms), 5_000)
        samples_per_chunk = int(self.sample_rate * self.chunk_ms / 1000)
        emitted_ms = 0
        phase = 0
        while emitted_ms < total_ms:
            buf = bytearray()
            for _ in range(samples_per_chunk):
                value = int(12000 * math.sin(2 * math.pi * self.freq * phase / self.sample_rate))
                buf += struct.pack("<h", value)
                phase += 1
            await asyncio.sleep(self.chunk_delay_s)
            yield AudioChunk(pcm=bytes(buf), sample_rate=self.sample_rate)
            emitted_ms += self.chunk_ms
