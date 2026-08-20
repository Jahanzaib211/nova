/**
 * Microphone capture: the AudioWorklet, shared by the live session and the
 * settings panel's "test microphone" control.
 *
 * There is exactly one definition of the worklet and the frame geometry here.
 * The mic test is only a useful diagnostic if it exercises the *same* capture
 * path the real conversation uses — a test with its own copy would happily pass
 * while the real path is broken.
 */

import { toPcm16 } from "./playback";

export const CAPTURE_RATE = 16000;

/** 20 ms frames — what the server's VAD expects. */
export const FRAME_SAMPLES = (CAPTURE_RATE * 20) / 1000;

/**
 * Worklet source. It only forwards raw frames; every decision (is this speech?
 * has the turn ended?) is made server-side so there is exactly one
 * implementation of the turn-taking rules.
 */
export const WORKLET_SOURCE = `
class NovaCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) {
      // Copy: the render quantum buffer is reused after this returns.
      this.port.postMessage(new Float32Array(channel));
    }
    return true;
  }
}
registerProcessor('nova-capture', NovaCaptureProcessor);
`;

/** Wrap raw PCM16 mono in a WAV header so the server can decode it with no ffmpeg. */
export function pcm16ToWav(pcm: Uint8Array, sampleRate: number): Blob {
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const put = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++)
      view.setUint8(offset + i, text.charCodeAt(i));
  };
  put(0, "RIFF");
  view.setUint32(4, 36 + pcm.length, true);
  put(8, "WAVEfmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  put(36, "data");
  view.setUint32(40, pcm.length, true);
  return new Blob([header, pcm as BlobPart], { type: "audio/wav" });
}

/**
 * Record `seconds` of microphone audio as 16 kHz PCM16, via the real worklet.
 *
 * Deliberately not MediaRecorder: that produces WebM/Opus, which the server
 * would need ffmpeg to decode — and ffmpeg is absent from the running gateway
 * image. Raw PCM keeps the diagnostic working wherever the live session works.
 */
export async function recordPcm16(
  seconds: number,
  signal?: AbortSignal,
): Promise<{ pcm: Uint8Array; sampleRate: number }> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  const ctx = new AudioContext({ sampleRate: CAPTURE_RATE });
  let blobUrl: string | null = null;
  const chunks: Uint8Array[] = [];

  try {
    blobUrl = URL.createObjectURL(
      new Blob([WORKLET_SOURCE], { type: "application/javascript" }),
    );
    await ctx.audioWorklet.addModule(blobUrl);

    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "nova-capture");

    let carry = new Float32Array(0);
    node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
      const merged = new Float32Array(carry.length + ev.data.length);
      merged.set(carry);
      merged.set(ev.data, carry.length);
      let offset = 0;
      while (merged.length - offset >= FRAME_SAMPLES) {
        chunks.push(
          new Uint8Array(
            toPcm16(
              merged.subarray(offset, offset + FRAME_SAMPLES),
              ctx.sampleRate,
              CAPTURE_RATE,
            ),
          ),
        );
        offset += FRAME_SAMPLES;
      }
      carry = merged.slice(offset);
    };
    // Not connected to destination: routing the mic to the speakers would
    // create a feedback loop.
    source.connect(node);

    await new Promise<void>((resolve) => {
      const done = () => resolve();
      signal?.addEventListener("abort", done, { once: true });
      setTimeout(done, seconds * 1000);
    });

    node.port.onmessage = null;
    source.disconnect();

    const total = chunks.reduce((n, c) => n + c.length, 0);
    const pcm = new Uint8Array(total);
    let at = 0;
    for (const c of chunks) {
      pcm.set(c, at);
      at += c.length;
    }
    return { pcm, sampleRate: CAPTURE_RATE };
  } finally {
    // Order matters: stop the tracks before closing the context, or the mic
    // indicator can stay lit in the browser chrome.
    stream.getTracks().forEach((t) => t.stop());
    await ctx.close().catch(() => undefined);
    if (blobUrl) URL.revokeObjectURL(blobUrl);
  }
}
