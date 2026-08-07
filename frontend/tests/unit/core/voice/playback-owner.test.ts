/**
 * One clip at a time.
 *
 * `HTMLAudioElement.play()` resolves when playback *starts*, not when it ends.
 * A caller that awaits it and immediately plays the next clip therefore stacks
 * them — auditioning eight voices played all eight simultaneously, which reads
 * as "the lab glitches" rather than as a bug with an obvious cause. And an
 * aborted fetch does nothing to audio already playing, so Stop stopped nothing.
 *
 * These pin the invariants rather than the implementation: only one element is
 * ever unpaused, `awaitPlayback` really waits for `ended`, and stopping is
 * audible immediately.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stopSpeaking, testSpeaker } from "@/core/voice/config";

type FakeAudio = {
  paused: boolean;
  src: string;
  play: () => Promise<void>;
  pause: () => void;
  addEventListener: (type: string, fn: () => void, opts?: unknown) => void;
  end: () => void;
};

let created: FakeAudio[] = [];

function installFakes() {
  created = [];

  class Audio implements FakeAudio {
    paused = true;
    src: string;
    private handlers: Record<string, (() => void)[]> = {};

    constructor(src: string) {
      this.src = src;
      created.push(this);
    }
    play() {
      this.paused = false;
      return Promise.resolve();
    }
    pause() {
      this.paused = true;
    }
    addEventListener(type: string, fn: () => void) {
      (this.handlers[type] ??= []).push(fn);
    }
    /** Simulate the clip finishing. */
    end() {
      this.paused = true;
      this.handlers.ended?.forEach((fn) => fn());
    }
  }

  vi.stubGlobal("Audio", Audio as unknown as typeof globalThis.Audio);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: (_b: Blob) => `blob:clip-${created.length}`,
    revokeObjectURL: () => undefined,
  });

  // 44-byte WAV header declaring 16000 Hz and 32000 bytes of data == 1.0 s.
  const wav = new ArrayBuffer(44 + 32000);
  const view = new DataView(wav);
  view.setUint32(24, 16000, true);
  view.setUint32(40, 32000, true);

  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, arrayBuffer: async () => wav })),
  );
}

beforeEach(installFakes);
afterEach(() => {
  stopSpeaking();
  vi.unstubAllGlobals();
});

const playing = () => created.filter((a) => !a.paused);

describe("test playback has a single owner", () => {
  it("never leaves two clips playing at once", async () => {
    await testSpeaker("first");
    expect(playing()).toHaveLength(1);

    await testSpeaker("second");
    // The point of the fix: the first clip is stopped, not layered under.
    expect(playing()).toHaveLength(1);
    expect(created).toHaveLength(2);
    expect(created[0]!.paused).toBe(true);
  });

  it("stopSpeaking silences audio immediately", async () => {
    await testSpeaker("hello");
    expect(playing()).toHaveLength(1);

    stopSpeaking();
    expect(playing()).toHaveLength(0);
  });

  it("stopSpeaking is safe when nothing is playing", () => {
    expect(() => {
      stopSpeaking();
      stopSpeaking();
    }).not.toThrow();
  });
});

describe("awaitPlayback", () => {
  it("resolves only after the clip ends", async () => {
    let settled = false;
    const pending = testSpeaker("a sentence", undefined, { awaitPlayback: true }).then((r) => {
      settled = true;
      return r;
    });

    // Let the fetch resolve and playback start.
    await vi.waitFor(() => expect(created).toHaveLength(1));
    expect(settled).toBe(false);

    created[0]!.end();
    const result = await pending;
    expect(settled).toBe(true);
    expect(result.ok).toBe(true);
  });

  it("returns as soon as audio starts when not awaiting playback", async () => {
    const result = await testSpeaker("a sentence");
    // Still playing — the caller was not made to wait for it.
    expect(result.ok).toBe(true);
    expect(playing()).toHaveLength(1);
  });

  it("measures latency to first audio, not to the end of playback", async () => {
    const result = await testSpeaker("a sentence", undefined, { awaitPlayback: false });
    // 1.0s of audio; a latency anywhere near that would mean we timed playback.
    expect(result.durationS).toBeCloseTo(1.0, 1);
    expect(result.latencyMs).toBeLessThan(500);
  });
});

describe("autoplay refusal", () => {
  it("is reported as blocked, not as a failure", async () => {
    // Firefox blocks media autoplay by default. Reporting that as an engine
    // failure sent people hunting a backend bug when the fix is one click.
    vi.stubGlobal(
      "Audio",
      class {
        paused = true;
        src = "";
        constructor(src: string) {
          this.src = src;
        }
        play() {
          return Promise.reject(new DOMException("blocked", "NotAllowedError"));
        }
        pause() {}
        addEventListener() {}
      } as unknown as typeof globalThis.Audio,
    );

    const result = await testSpeaker("hello", undefined, { awaitPlayback: true });

    expect(result.ok).toBe(true); // the audio arrived — that part worked
    expect(result.blocked).toBe(true);
    expect(typeof result.play).toBe("function"); // a click can replay it
  });
});
