import { describe, expect, it } from "vitest";

import {
  CAPTURE_RATE,
  FRAME_SAMPLES,
  WORKLET_SOURCE,
  pcm16ToWav,
} from "@/core/voice/capture";

/** Read a WAV header field without assuming the helper is correct. */
async function header(blob: Blob) {
  const view = new DataView(await blob.arrayBuffer());
  const ascii = (off: number, len: number) =>
    String.fromCharCode(
      ...Array.from({ length: len }, (_, i) => view.getUint8(off + i)),
    );
  return {
    riff: ascii(0, 4),
    riffSize: view.getUint32(4, true),
    wave: ascii(8, 4),
    fmt: ascii(12, 4),
    audioFormat: view.getUint16(20, true),
    channels: view.getUint16(22, true),
    sampleRate: view.getUint32(24, true),
    byteRate: view.getUint32(28, true),
    blockAlign: view.getUint16(32, true),
    bitsPerSample: view.getUint16(34, true),
    data: ascii(36, 4),
    dataSize: view.getUint32(40, true),
  };
}

describe("capture geometry", () => {
  it("uses 20 ms frames, which is what the server VAD expects", () => {
    expect(CAPTURE_RATE).toBe(16000);
    expect(FRAME_SAMPLES).toBe(320);
  });

  it("registers the processor name the session constructs", () => {
    // A mismatch here fails only at runtime, inside an AudioWorklet, where the
    // error is close to invisible.
    expect(WORKLET_SOURCE).toContain("registerProcessor('nova-capture'");
  });
});

describe("pcm16ToWav", () => {
  const pcm = new Uint8Array(640); // 320 samples of silence

  it("produces a header the server's `wave` module can parse", async () => {
    const h = await header(pcm16ToWav(pcm, 16000));
    expect(h.riff).toBe("RIFF");
    expect(h.wave).toBe("WAVE");
    expect(h.data).toBe("data");
    expect(h.audioFormat).toBe(1); // PCM
    expect(h.channels).toBe(1); // mono — the server rejects anything else
    expect(h.bitsPerSample).toBe(16);
  });

  it("declares sizes that match the payload", async () => {
    const h = await header(pcm16ToWav(pcm, 16000));
    // A wrong dataSize is the classic WAV bug: players and decoders either
    // truncate or read past the end, and neither reports why.
    expect(h.dataSize).toBe(pcm.length);
    expect(h.riffSize).toBe(36 + pcm.length);
  });

  it("derives byte rate and block align from the sample rate", async () => {
    const h = await header(pcm16ToWav(pcm, 24000));
    expect(h.sampleRate).toBe(24000);
    expect(h.byteRate).toBe(24000 * 2);
    expect(h.blockAlign).toBe(2);
  });

  it("emits exactly 44 header bytes plus the payload", async () => {
    const blob = pcm16ToWav(pcm, 16000);
    expect(blob.size).toBe(44 + pcm.length);
    expect(blob.type).toBe("audio/wav");
  });

  it("round-trips the sample data unchanged", async () => {
    const payload = new Uint8Array([1, 0, 255, 127, 0, 128]);
    const buf = await pcm16ToWav(payload, 16000).arrayBuffer();
    expect(new Uint8Array(buf.slice(44))).toEqual(payload);
  });
});
