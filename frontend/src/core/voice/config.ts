/**
 * Voice settings: read, write, and prove it works.
 *
 * The "prove it works" part is why this module exists rather than a bare fetch.
 * A settings panel that only writes values is untrustworthy — the engines are
 * server-side process singletons loaded from model weights on disk, so a
 * setting can be saved perfectly and still not work (missing weights, no GPU,
 * an engine that fails to warm up). `testSpeaker` and `testMicrophone` exercise
 * the real paths and report what actually happened.
 */

import { fetch as apiFetch } from "@/core/api/fetcher";

import { pcm16ToWav, recordPcm16 } from "./capture";

export type EngineChoice = {
  use: string;
  label: string;
  note?: string;
  languages?: string;
  models?: string[];
  voices?: string[];
  default?: boolean;
};

export type EngineLive = {
  name: string;
  /** What config asked for — often "auto". */
  device_requested: string | null;
  /** What the engine actually resolved to. The interesting one. */
  device_actual: string | null;
  model: string | null;
  compute_type: string | null;
  sample_rate: number | null;
};

/**
 * Engine settings are typed rather than `Record<string, unknown>`.
 *
 * The registry forwards arbitrary keys to the engine constructor, so the wire
 * format really is open-ended — but leaving it untyped here makes every field
 * `unknown` at the call site, which forces `String(...)` casts that silently
 * stringify objects as "[object Object]". Naming the fields the UI actually
 * edits keeps that honest; `[key: string]` preserves the pass-through.
 */
export type SttSettings = {
  use?: string;
  model?: string;
  device?: string;
  compute_type?: string;
  language?: string | null;
  [key: string]: unknown;
};

export type TtsSettings = {
  use?: string;
  voice?: string;
  device?: string;
  speed?: number;
  [key: string]: unknown;
};

export type VoiceSettings = {
  enabled?: boolean;
  stt?: SttSettings;
  tts?: TtsSettings;
  vad?: Record<string, unknown>;
};

export type VoiceConfig = {
  settings: VoiceSettings;
  catalog: { stt: EngineChoice[]; tts: EngineChoice[] };
  overrides_path: string;
  has_overrides: boolean;
  live?: { stt: EngineLive; tts: EngineLive };
};

export type VoiceStatus = {
  enabled: boolean;
  ready?: boolean;
  reason?: string;
  stt?: string;
  tts?: string;
  sample_rate?: number;
};

export async function getVoiceConfig(): Promise<VoiceConfig> {
  const res = await apiFetch("/api/voice/config");
  if (!res.ok) throw new Error(`Could not load voice settings (HTTP ${res.status})`);
  return (await res.json()) as VoiceConfig;
}

export async function putVoiceConfig(settings: VoiceSettings): Promise<VoiceConfig> {
  const res = await apiFetch("/api/voice/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!res.ok) {
    // The server validates engine class paths and explains what is wrong;
    // surfacing its message beats a generic failure.
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `Could not save voice settings (HTTP ${res.status})`);
  }
  return (await res.json()) as VoiceConfig;
}

export async function resetVoiceConfig(): Promise<VoiceConfig> {
  const res = await apiFetch("/api/voice/config", { method: "DELETE" });
  if (!res.ok) throw new Error(`Could not reset voice settings (HTTP ${res.status})`);
  return (await res.json()) as VoiceConfig;
}

/**
 * `refresh` forces the server to re-warm the engines rather than answer from
 * its cache — which is what you want right after changing a setting.
 */
export async function getVoiceStatus(refresh = false): Promise<VoiceStatus> {
  const res = await apiFetch(`/api/voice/status${refresh ? "?refresh=1" : ""}`);
  if (!res.ok) throw new Error(`Could not read voice status (HTTP ${res.status})`);
  return (await res.json()) as VoiceStatus;
}

export type SpeakerTest = {
  ok: boolean;
  /** Server-side synthesis time, measured client-side round trip. */
  latencyMs: number;
  /** Audio seconds produced per second of wall clock. <1 means slower than real time. */
  rtf: number | null;
  durationS: number | null;
  error?: string;
};

/** Decode enough of a WAV header to know how long the audio is. */
function wavDurationSeconds(buf: ArrayBuffer): number | null {
  if (buf.byteLength < 44) return null;
  const view = new DataView(buf);
  const sampleRate = view.getUint32(24, true);
  const dataSize = view.getUint32(40, true);
  if (!sampleRate || !dataSize) return null;
  return dataSize / 2 / sampleRate; // PCM16 mono
}

/**
 * Synthesize a phrase, play it, and report real numbers.
 *
 * Reporting RTF matters: Kokoro's int8 build runs *slower than real time* on
 * CPU, which sounds like stuttering rather than an obvious error. A number the
 * user can see makes that diagnosable instead of mysterious.
 */
export async function testSpeaker(text: string, signal?: AbortSignal): Promise<SpeakerTest> {
  const started = performance.now();
  try {
    const res = await apiFetch("/api/voice/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      return { ok: false, latencyMs: performance.now() - started, rtf: null, durationS: null, error: detail?.detail ?? `HTTP ${res.status}` };
    }

    const buf = await res.arrayBuffer();
    const latencyMs = performance.now() - started;
    const durationS = wavDurationSeconds(buf);

    const url = URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
    const audio = new Audio(url);
    audio.addEventListener("ended", () => URL.revokeObjectURL(url), { once: true });
    // Autoplay may be refused if this was not triggered by a click. The test is
    // still meaningful — the audio arrived — so a refusal is not a failure.
    await audio.play().catch(() => undefined);

    return {
      ok: true,
      latencyMs,
      durationS,
      rtf: durationS ? latencyMs / 1000 / durationS : null,
    };
  } catch (e) {
    return { ok: false, latencyMs: performance.now() - started, rtf: null, durationS: null, error: String(e) };
  }
}

export type MicTest = { ok: boolean; heard?: string; durationS?: number; error?: string };

/**
 * Record a few seconds, send it to the real STT engine, show the words back.
 *
 * The only check that covers the whole capture path — permission, device,
 * sample rate, worklet and engine — in one action. It records through
 * `recordPcm16`, the *same* AudioWorklet the live session uses, and uploads raw
 * PCM in a WAV wrapper: MediaRecorder's WebM would need ffmpeg server-side, and
 * ffmpeg is absent from the running gateway image.
 */
export async function testMicrophone(seconds = 4, signal?: AbortSignal): Promise<MicTest> {
  try {
    const { pcm, sampleRate } = await recordPcm16(seconds, signal);
    if (!pcm.length) {
      return { ok: false, error: "No audio was captured — check the input device." };
    }

    const body = new FormData();
    body.append("audio", pcm16ToWav(pcm, sampleRate), "mic-test.wav");

    const res = await apiFetch("/api/voice/transcribe", { method: "POST", body });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      return { ok: false, error: detail?.detail ?? `HTTP ${res.status}` };
    }
    const data = (await res.json()) as { text?: string; duration_s?: number };
    return { ok: true, heard: data.text ?? "", durationS: data.duration_s };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}
