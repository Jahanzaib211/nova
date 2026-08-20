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

/** Semantic endpointing. Opt-in: see `speech/turn.py`. */
export type TurnSettings = {
  use?: string;
  enabled?: boolean;
  threshold?: number;
  device?: string;
  [key: string]: unknown;
};

export type VoiceSettings = {
  enabled?: boolean;
  stt?: SttSettings;
  tts?: TtsSettings;
  turn?: TurnSettings;
  vad?: Record<string, unknown>;
};

export type VoiceConfig = {
  settings: VoiceSettings;
  catalog: { stt: EngineChoice[]; tts: EngineChoice[]; turn?: EngineChoice[] };
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

/**
 * Normalize whatever the server sent into a shape the UI can render.
 *
 * The panel renders `config.catalog.stt` directly, so a response missing
 * `catalog` crashes it — which is exactly what happened when PUT replied with a
 * different shape than GET. The server now returns one shape for all three
 * verbs, and this is the belt to that braces: a settings screen that white-
 * screens is worse than one showing an empty list.
 */
function normalizeConfig(raw: unknown): VoiceConfig {
  const data = (raw ?? {}) as Partial<VoiceConfig>;
  const catalog = data.catalog ?? ({} as VoiceConfig["catalog"]);
  return {
    settings: data.settings ?? {},
    catalog: {
      stt: catalog.stt ?? [],
      tts: catalog.tts ?? [],
      turn: catalog.turn ?? [],
    },
    overrides_path: data.overrides_path ?? "",
    has_overrides: Boolean(data.has_overrides),
    live: data.live,
  };
}

export async function getVoiceConfig(): Promise<VoiceConfig> {
  const res = await apiFetch("/api/voice/config");
  if (!res.ok)
    throw new Error(`Could not load voice settings (HTTP ${res.status})`);
  return normalizeConfig(await res.json());
}

export async function putVoiceConfig(
  settings: VoiceSettings,
): Promise<VoiceConfig> {
  const res = await apiFetch("/api/voice/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!res.ok) {
    // The server validates engine class paths and explains what is wrong;
    // surfacing its message beats a generic failure.
    const detail = await res.json().catch(() => null);
    throw new Error(
      detail?.detail ?? `Could not save voice settings (HTTP ${res.status})`,
    );
  }
  return normalizeConfig(await res.json());
}

export async function resetVoiceConfig(): Promise<VoiceConfig> {
  const res = await apiFetch("/api/voice/config", { method: "DELETE" });
  if (!res.ok)
    throw new Error(`Could not reset voice settings (HTTP ${res.status})`);
  return normalizeConfig(await res.json());
}

/**
 * `refresh` forces the server to re-warm the engines rather than answer from
 * its cache — which is what you want right after changing a setting.
 */
export async function getVoiceStatus(refresh = false): Promise<VoiceStatus> {
  const res = await apiFetch(`/api/voice/status${refresh ? "?refresh=1" : ""}`);
  if (!res.ok)
    throw new Error(`Could not read voice status (HTTP ${res.status})`);
  return (await res.json()) as VoiceStatus;
}

export type SpeakerTest = {
  ok: boolean;
  /**
   * The audio arrived but the browser refused to start it without a user
   * gesture. Firefox blocks media autoplay by default, so this is common and
   * is NOT a failure of the voice stack — reporting it as one sent people
   * hunting a backend bug when the fix is one click. `play()` replays it from
   * inside a real click handler, where the browser allows it.
   */
  blocked?: boolean;
  play?: () => Promise<void>;
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
 * The single owner of test playback.
 *
 * Without one, overlapping audio is the default: `HTMLAudioElement.play()`
 * resolves when playback *starts*, not when it ends, so a caller that awaits it
 * and immediately plays the next clip stacks them. Auditioning eight voices did
 * exactly that — eight voices talking simultaneously, which reads as "the lab
 * glitches" rather than as a bug in the lab.
 *
 * One element at a time, stopped before the next begins, and stoppable from
 * outside so the Stop button silences audio instead of only cancelling a fetch.
 */
let current: { audio: HTMLAudioElement; url: string } | null = null;

export function stopSpeaking(): void {
  if (!current) return;
  const { audio, url } = current;
  current = null;
  audio.pause();
  audio.src = "";
  URL.revokeObjectURL(url);
}

/**
 * Resolves when playback finishes (or fails, or is stopped), reporting whether
 * the browser refused to start it.
 */
function playToCompletion(
  url: string,
  signal?: AbortSignal,
): { done: Promise<{ blocked: boolean }>; audio: HTMLAudioElement } {
  stopSpeaking();
  const audio = new Audio(url);
  current = { audio, url };

  const done = new Promise<{ blocked: boolean }>((resolve) => {
    const finish = (blocked = false) => {
      if (current?.audio === audio) {
        current = null;
        URL.revokeObjectURL(url);
      }
      resolve({ blocked });
    };
    audio.addEventListener("ended", () => finish(), { once: true });
    audio.addEventListener("error", () => finish(), { once: true });
    signal?.addEventListener(
      "abort",
      () => {
        audio.pause();
        finish();
      },
      { once: true },
    );

    // Firefox blocks media autoplay by default and Chrome does on low
    // engagement. The audio arrived either way, so this is a browser policy
    // outcome, not a broken engine — say which so the UI can offer a click.
    void audio.play().catch(() => finish(true));
  });

  return { done, audio };
}

/**
 * Synthesize a phrase, play it, and report real numbers.
 *
 * `awaitPlayback` matters for sequences: without it the caller races ahead and
 * the clips overlap. Latency is always measured to *first audio* — the number
 * a user feels — not to the end of playback.
 *
 * Reporting RTF matters too: Kokoro's int8 build runs *slower than real time*
 * on CPU, which sounds like stuttering rather than an obvious error. A number
 * the user can see makes that diagnosable instead of mysterious.
 */
export async function testSpeaker(
  text: string,
  signal?: AbortSignal,
  opts: { awaitPlayback?: boolean } = {},
): Promise<SpeakerTest> {
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
      return {
        ok: false,
        latencyMs: performance.now() - started,
        rtf: null,
        durationS: null,
        error: detail?.detail ?? `HTTP ${res.status}`,
      };
    }

    const buf = await res.arrayBuffer();
    const latencyMs = performance.now() - started;
    const durationS = wavDurationSeconds(buf);
    const url = URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));

    const { done, audio } = playToCompletion(url, signal);
    const base = {
      ok: true as const,
      latencyMs,
      durationS,
      rtf: durationS ? latencyMs / 1000 / durationS : null,
    };

    if (!opts.awaitPlayback) {
      // Give the browser a moment to reject autoplay so the caller learns about
      // it; without this the answer always looks like "playing fine".
      const settled = await Promise.race([
        done,
        new Promise<null>((r) => setTimeout(() => r(null), 150)),
      ]);
      if (settled?.blocked)
        return { ...base, blocked: true, play: () => audio.play() };
      return base;
    }

    const { blocked } = await done;
    return blocked
      ? { ...base, blocked: true, play: () => audio.play() }
      : base;
  } catch (e) {
    // An aborted fetch is a deliberate stop, not an error to shout about.
    if (signal?.aborted)
      return {
        ok: false,
        latencyMs: performance.now() - started,
        rtf: null,
        durationS: null,
        error: "stopped",
      };
    return {
      ok: false,
      latencyMs: performance.now() - started,
      rtf: null,
      durationS: null,
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

export type MicTest = {
  ok: boolean;
  heard?: string;
  durationS?: number;
  error?: string;
};

/**
 * Record a few seconds, send it to the real STT engine, show the words back.
 *
 * The only check that covers the whole capture path — permission, device,
 * sample rate, worklet and engine — in one action. It records through
 * `recordPcm16`, the *same* AudioWorklet the live session uses, and uploads raw
 * PCM in a WAV wrapper: MediaRecorder's WebM would need ffmpeg server-side, and
 * ffmpeg is absent from the running gateway image.
 */
export async function testMicrophone(
  seconds = 4,
  signal?: AbortSignal,
): Promise<MicTest> {
  try {
    const { pcm, sampleRate } = await recordPcm16(seconds, signal);
    if (!pcm.length) {
      return {
        ok: false,
        error: "No audio was captured — check the input device.",
      };
    }

    const body = new FormData();
    body.append("audio", pcm16ToWav(pcm, sampleRate), "mic-test.wav");

    const res = await apiFetch("/api/voice/transcribe", {
      method: "POST",
      body,
    });
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
