/**
 * Voice: state machine, PCM conversion, and the interruptible playback queue.
 *
 * The state machine and the converter are pure, so they're tested directly.
 * The player is tested against a minimal fake AudioContext — the real one needs
 * a browser, but the behaviour that matters (schedule back-to-back, stop
 * everything on interrupt) is ours, not the browser's.
 */

import { beforeEach, describe, expect, test, vi } from "vitest";

import { VoicePlayer, toPcm16 } from "@/core/voice/playback";
import {
  initialVoiceState,
  isLive,
  phaseLabel,
  voiceReducer,
  type VoiceState,
} from "@/core/voice/state";

function reduce(events: Parameters<typeof voiceReducer>[1][]): VoiceState {
  return events.reduce(voiceReducer, initialVoiceState);
}

describe("voice state machine", () => {
  test("a full turn walks idle -> listening -> thinking -> speaking -> idle", () => {
    const phases = [
      { type: "ready" as const },
      { type: "listening" as const },
      { type: "thinking" as const },
      { type: "speaking" as const },
      { type: "idle" as const },
    ].map((_, i, all) => reduce(all.slice(0, i + 1)).phase);

    expect(phases).toEqual([
      "idle",
      "listening",
      "thinking",
      "speaking",
      "idle",
    ]);
  });

  test("ready adopts the server's sample rate", () => {
    expect(reduce([{ type: "ready", sample_rate: 24000 }]).sampleRate).toBe(
      24000,
    );
  });

  test("interrupt records barge-in without pretending the turn ended", () => {
    // The user is mid-utterance when this arrives; jumping to idle would make
    // the UI flicker "Ready" between their own words.
    const state = reduce([
      { type: "ready" },
      { type: "speaking" },
      { type: "interrupt" },
    ]);
    expect(state.interrupted).toBe(true);
    expect(state.phase).toBe("speaking");
  });

  test("starting to talk clears the interrupt latch", () => {
    const state = reduce([
      { type: "ready" },
      { type: "speaking" },
      { type: "interrupt" },
      { type: "listening" },
    ]);
    expect(state.interrupted).toBe(false);
    expect(state.phase).toBe("listening");
  });

  test("assistant chunks accumulate within one turn", () => {
    const state = reduce([
      { type: "speaking" },
      { type: "assistant", text: "Hello." },
      { type: "assistant", text: "How can I help?" },
    ]);
    expect(state.assistant).toBe("Hello. How can I help?");
  });

  test("a new speaking turn starts a fresh assistant message", () => {
    const state = reduce([
      { type: "speaking" },
      { type: "assistant", text: "First answer." },
      { type: "idle" },
      { type: "speaking" },
    ]);
    expect(state.assistant).toBe("");
  });

  test("transcript is recorded without changing phase", () => {
    const state = reduce([
      { type: "listening" },
      { type: "transcript", text: "what is the weather", final: true },
    ]);
    expect(state.transcript).toBe("what is the weather");
    expect(state.phase).toBe("listening");
  });

  test("error is terminal-ish and carries its message", () => {
    const state = reduce([{ type: "error", message: "mic denied" }]);
    expect(state.phase).toBe("error");
    expect(state.error).toBe("mic denied");
    expect(isLive(state)).toBe(false);
  });

  test("reconnecting clears a previous error", () => {
    const state = reduce([
      { type: "error", message: "boom" },
      { type: "connecting" },
    ]);
    expect(state.error).toBeNull();
  });

  test("closed is not live", () => {
    expect(isLive(reduce([{ type: "ready" }]))).toBe(true);
    expect(isLive(reduce([{ type: "ready" }, { type: "closed" }]))).toBe(false);
  });

  test("phase labels are human", () => {
    expect(phaseLabel(reduce([{ type: "ready" }]))).toBe("Ready");
    expect(phaseLabel(reduce([{ type: "listening" }]))).toBe("Listening");
    expect(phaseLabel(reduce([{ type: "speaking" }]))).toBe("Speaking");
  });

  test("an unknown event leaves state untouched", () => {
    const before = reduce([{ type: "ready" }]);
    // @ts-expect-error deliberately invalid
    expect(voiceReducer(before, { type: "nonsense" })).toBe(before);
  });
});

describe("toPcm16", () => {
  test("downsamples 48kHz to 16kHz by a factor of three", () => {
    const input = new Float32Array(300);
    const out = new Int16Array(toPcm16(input, 48000, 16000));
    expect(out.length).toBe(100);
  });

  test("full scale maps to the int16 extremes without wrapping", () => {
    const out = new Int16Array(
      toPcm16(new Float32Array([1, -1]), 16000, 16000),
    );
    expect(out[0]).toBe(32767);
    expect(out[1]).toBe(-32768);
  });

  test("out-of-range input is clamped, not wrapped", () => {
    // Wrapping would turn a loud sample into a loud sample of the OPPOSITE
    // sign — audible as a nasty click.
    const out = new Int16Array(
      toPcm16(new Float32Array([2, -2]), 16000, 16000),
    );
    expect(out[0]).toBe(32767);
    expect(out[1]).toBe(-32768);
  });

  test("silence stays silent", () => {
    const out = new Int16Array(toPcm16(new Float32Array(64), 16000, 16000));
    expect([...out].every((s) => s === 0)).toBe(true);
  });
});

// ── Playback queue ───────────────────────────────────────────────────────────

class FakeSource {
  started: number | null = null;
  stopped = false;
  onended: (() => void) | null = null;
  buffer: { duration: number } | null = null;
  connect = vi.fn();
  disconnect = vi.fn();
  start = (when: number) => {
    this.started = when;
  };
  stop = () => {
    if (this.stopped) throw new Error("already stopped");
    this.stopped = true;
    this.onended?.();
  };
}

class FakeContext {
  currentTime = 0;
  state = "running";
  destination = {};
  sources: FakeSource[] = [];
  createBuffer(_ch: number, length: number, rate: number) {
    return {
      duration: length / rate,
      getChannelData: () => new Float32Array(length),
    };
  }
  createBufferSource() {
    const s = new FakeSource();
    this.sources.push(s);
    return s;
  }
  close = vi.fn(async () => undefined);
}

let ctx: FakeContext;

beforeEach(() => {
  ctx = new FakeContext();
  // Must be constructible — the player does `new AudioContext(...)`, and an
  // arrow function cannot be used with `new`.
  vi.stubGlobal("AudioContext", function AudioContextStub(this: unknown) {
    return ctx;
  } as unknown as typeof AudioContext);
});

function pcm(samples: number): ArrayBuffer {
  return new Int16Array(samples).buffer;
}

describe("VoicePlayer", () => {
  test("schedules chunks back-to-back so there are no audible gaps", () => {
    const player = new VoicePlayer({ sampleRate: 16000, leadTimeS: 0.1 });
    player.enqueue(pcm(16000)); // 1s
    player.enqueue(pcm(16000)); // 1s

    const [first, second] = ctx.sources;
    expect(first!.started).toBeCloseTo(0.1);
    // Second starts exactly where the first ends, not on arrival.
    expect(second!.started).toBeCloseTo(1.1);
  });

  test("interrupt stops every scheduled chunk — this is barge-in", () => {
    const player = new VoicePlayer({ sampleRate: 16000 });
    player.enqueue(pcm(16000));
    player.enqueue(pcm(16000));
    expect(player.pending).toBe(2);

    player.interrupt();

    expect(ctx.sources.every((s) => s.stopped)).toBe(true);
    expect(player.pending).toBe(0);
  });

  test("audio queued after an interrupt starts fresh, not in the past", () => {
    const player = new VoicePlayer({ sampleRate: 16000, leadTimeS: 0.1 });
    player.enqueue(pcm(16000));
    player.interrupt();
    ctx.currentTime = 5;
    player.enqueue(pcm(16000));

    const last = ctx.sources.at(-1)!;
    // Scheduling in the past would play everything at once.
    expect(last.started).toBeGreaterThanOrEqual(5);
  });

  test("interrupting twice is safe", () => {
    const player = new VoicePlayer({ sampleRate: 16000 });
    player.enqueue(pcm(1600));
    player.interrupt();
    expect(() => player.interrupt()).not.toThrow();
  });

  test("finished chunks stop counting as pending", () => {
    const player = new VoicePlayer({ sampleRate: 16000 });
    player.enqueue(pcm(1600));
    expect(player.pending).toBe(1);
    ctx.sources[0]!.onended?.();
    expect(player.pending).toBe(0);
  });

  test("an empty chunk is ignored rather than scheduled", () => {
    const player = new VoicePlayer({ sampleRate: 16000 });
    player.enqueue(new ArrayBuffer(0));
    expect(player.pending).toBe(0);
  });
});
