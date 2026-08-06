/**
 * Streaming PCM playback with an interruptible queue.
 *
 * This is the piece barge-in depends on. Audio handed to the sound device
 * cannot be retracted, so we schedule each chunk as its own
 * `AudioBufferSourceNode` and keep references to every one that has not
 * finished. Interrupting stops all of them and resets the schedule clock —
 * something an `<audio>` element with a streaming source cannot do cleanly.
 *
 * Chunks are scheduled back-to-back against `nextStartTime` rather than played
 * on arrival, so a jittery network doesn't produce audible gaps between them.
 */

export type PlaybackOptions = {
  sampleRate?: number;
  /** Small lead so the first chunk isn't scheduled in the past. */
  leadTimeS?: number;
};

export class VoicePlayer {
  private ctx: AudioContext | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private nextStartTime = 0;
  private readonly sampleRate: number;
  private readonly leadTimeS: number;

  constructor(options: PlaybackOptions = {}) {
    this.sampleRate = options.sampleRate ?? 24000;
    this.leadTimeS = options.leadTimeS ?? 0.08;
  }

  /** Number of chunks scheduled but not yet finished. Exposed for tests. */
  get pending(): number {
    return this.sources.size;
  }

  private context(): AudioContext {
    this.ctx ??= new AudioContext({ sampleRate: this.sampleRate });
    return this.ctx;
  }

  /** Queue one chunk of mono PCM16 for playback. */
  enqueue(pcm: ArrayBuffer): void {
    const ctx = this.context();
    const samples = new Int16Array(pcm);
    if (samples.length === 0) return;

    const buffer = ctx.createBuffer(1, samples.length, this.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) {
      // Int16 -> float32 in [-1, 1). 32768 (not 32767) keeps the scale
      // symmetric so full-scale negatives don't clip.
      channel[i] = samples[i]! / 32768;
    }

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);

    const now = ctx.currentTime;
    // If we've fallen behind (first chunk, or a gap in the stream), restart the
    // schedule slightly ahead of now instead of in the past — scheduling in the
    // past plays everything at once.
    if (this.nextStartTime < now + this.leadTimeS / 2) {
      this.nextStartTime = now + this.leadTimeS;
    }
    source.start(this.nextStartTime);
    this.nextStartTime += buffer.duration;

    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
    };
  }

  /**
   * Stop everything immediately and drop what's queued.
   *
   * Called on barge-in. Stopping a source fires `onended`, which removes it
   * from the set, so we iterate a copy.
   */
  interrupt(): void {
    for (const source of [...this.sources]) {
      try {
        source.stop();
      } catch {
        // Already finished; stop() on a stopped source throws.
      }
      source.disconnect();
    }
    this.sources.clear();
    this.nextStartTime = 0;
  }

  /** Interrupt and release the audio context. */
  async close(): Promise<void> {
    this.interrupt();
    const ctx = this.ctx;
    this.ctx = null;
    if (ctx && ctx.state !== "closed") {
      try {
        await ctx.close();
      } catch {
        // Nothing useful to do if the context is already gone.
      }
    }
  }
}

/**
 * Downsample Float32 mic audio to 16 kHz PCM16.
 *
 * The browser captures at the device rate (usually 48 kHz); Whisper wants
 * 16 kHz, and sending more is wasted bandwidth on every frame. Exported
 * separately from the worklet so it can be unit tested.
 */
export function toPcm16(
  input: Float32Array,
  inputRate: number,
  outputRate = 16000,
): ArrayBuffer {
  const ratio = inputRate / outputRate;
  const outLength = Math.floor(input.length / ratio);
  const out = new Int16Array(outLength);
  for (let i = 0; i < outLength; i++) {
    // Nearest-neighbour is adequate here: speech is band-limited well below
    // 8 kHz and Whisper is robust to the artefacts a fancier filter would
    // remove. Cheap matters — this runs on every audio frame.
    const sample = input[Math.floor(i * ratio)] ?? 0;
    const clamped = Math.max(-1, Math.min(1, sample));
    out[i] = clamped < 0 ? clamped * 32768 : clamped * 32767;
  }
  return out.buffer;
}
