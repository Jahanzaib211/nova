"""Speech engine contracts: speech-to-text and text-to-speech.

Why these are not models
------------------------
``models/factory.py`` resolves every entry in ``config.yaml``'s ``models:``
through ``resolve_class(..., BaseChatModel)`` — a hard type gate. A Whisper
client is not a ``BaseChatModel``, so speech engines cannot live there at all,
and ``create_chat_model`` is saturated with chat-only semantics besides
(thinking toggles, token accounting, BYOK key override, tracing callbacks).

So speech gets its own small registry built on the *generic* reflection layer,
mirroring how ``sandbox.use`` is declared rather than how models are. See
``registry.py``.

Audio format
------------
One format throughout: **16-bit signed little-endian PCM, mono**. No containers,
no codecs. The browser captures via AudioWorklet and sends raw frames, so ffmpeg
is never in the realtime path — it is only needed to decode *uploaded* files.
Sample rate travels with the audio rather than being assumed, because STT wants
16 kHz and TTS engines typically emit 22.05 or 24 kHz.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

# The rate STT engines expect. Whisper resamples internally, but feeding it
# 16 kHz avoids a needless conversion on every utterance.
STT_SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class AudioChunk:
    """A slice of mono PCM16 audio."""

    pcm: bytes
    sample_rate: int

    @property
    def duration_s(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return len(self.pcm) / 2 / self.sample_rate  # 2 bytes per sample


@dataclass
class Transcript:
    """What an STT engine heard."""

    text: str
    language: str | None = None
    # Whisper reports an average log-probability; engines that have no notion of
    # confidence leave this None rather than inventing a number.
    confidence: float | None = None
    is_final: bool = True
    segments: list[dict] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.text.strip())


class SpeechToText(abc.ABC):
    """Turns PCM audio into text."""

    name: str = "stt"

    @abc.abstractmethod
    def transcribe(self, audio: AudioChunk) -> Transcript:
        """Transcribe one complete utterance. Must be safe to call repeatedly."""

    def warmup(self) -> None:
        """Optionally pre-load weights so the first real utterance isn't slow."""

    def close(self) -> None:
        """Release any held resources."""


class TextToSpeech(abc.ABC):
    """Turns text into PCM audio."""

    name: str = "tts"
    sample_rate: int = 24_000

    @abc.abstractmethod
    async def synthesize(self, text: str, *, voice: str | None = None) -> AsyncIterator[AudioChunk]:
        """Stream audio for ``text``.

        Streaming rather than returning one blob is what makes barge-in
        possible: the caller stops consuming and the generator is closed, so a
        long sentence is abandoned mid-word instead of being synthesized in full
        and then thrown away.
        """
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator for typing

    def warmup(self) -> None:
        """Optionally pre-load weights."""

    def close(self) -> None:
        """Release any held resources."""


class SpeechEngineUnavailable(RuntimeError):
    """An engine is configured but its dependency or model weights are missing.

    Carries an actionable install hint rather than a bare ImportError, matching
    how the reflection layer reports missing provider packages.
    """
