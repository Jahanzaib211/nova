"use client";

/**
 * Voice settings — engine selection, device placement, and controls that
 * actually prove the thing works.
 *
 * The test buttons are the point. Voice depends on server-side model weights,
 * an optional GPU, and microphone permission, so a saved setting can be
 * perfectly valid and still not work. Showing measured latency and the words
 * Nova actually heard turns "voice is broken" into a specific, fixable fact.
 *
 * Copy here is intentionally in English rather than i18n keys: these strings
 * name model files, devices and measured numbers, and a half-translated
 * diagnostic is worse than an untranslated one. The nav label is translated.
 */

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  Loader2Icon,
  MicIcon,
  RotateCcwIcon,
  Volume2Icon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  getVoiceConfig,
  getVoiceStatus,
  putVoiceConfig,
  resetVoiceConfig,
  stopSpeaking,
  testMicrophone,
  testSpeaker,
  type MicTest,
  type SpeakerTest,
  type VoiceConfig,
  type VoiceStatus,
} from "@/core/voice/config";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";
import { VoiceLab } from "./voice-lab";

const TEST_PHRASE = "Nova is online. All systems are green.";

/** Devices offered per engine. `auto` is the only one safe on every host. */
const DEVICES = [
  {
    value: "auto",
    label: "Auto",
    hint: "Use the GPU when it is really available, CPU otherwise.",
  },
  {
    value: "cuda",
    label: "GPU (CUDA)",
    hint: "Falls back to CPU with a warning if CUDA is unusable.",
  },
  { value: "cpu", label: "CPU", hint: "Never touch the GPU." },
];

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        {hint && <p className="text-muted-foreground mt-0.5 text-xs">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function Choice({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex gap-1">
      {options.map((o) => (
        <Button
          key={o.value}
          size="sm"
          variant={value === o.value ? "default" : "outline"}
          onClick={() => onChange(o.value)}
          className="h-7 text-xs"
        >
          {o.label}
        </Button>
      ))}
    </div>
  );
}

export function VoiceSettingsPage() {
  const [config, setConfig] = useState<VoiceConfig | null>(null);
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [speaker, setSpeaker] = useState<SpeakerTest | null>(null);
  const [mic, setMic] = useState<MicTest | null>(null);
  const [busy, setBusy] = useState<"speaker" | "mic" | null>(null);
  const abort = useRef<AbortController | null>(null);

  const refresh = useCallback(async (probe = false) => {
    try {
      const [cfg, st] = await Promise.all([
        getVoiceConfig(),
        getVoiceStatus(probe),
      ]);
      setConfig(cfg);
      setStatus(st);
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Aborting the fetch does not stop audio that already started playing;
    // closing the dialog mid-test would otherwise leave Nova talking.
    return () => {
      abort.current?.abort();
      stopSpeaking();
    };
  }, [refresh]);

  const save = useCallback(
    async (patch: Record<string, unknown>) => {
      if (!config) return;
      setSaving(true);
      try {
        const next = { ...config.settings, ...patch };
        const saved = await putVoiceConfig(next);
        setConfig(saved);
        // Re-probe rather than trusting the cached answer: the engines were
        // just dropped, so the old readiness describes objects that no longer
        // exist. This is the whole reason the panel can be trusted.
        setStatus(await getVoiceStatus(true));
        toast.success("Voice settings saved");
      } catch (e) {
        toast.error(
          e instanceof Error ? e.message : "Could not save voice settings",
        );
      } finally {
        setSaving(false);
      }
    },
    [config],
  );

  const section = useCallback(
    (name: "stt" | "tts" | "turn", patch: Record<string, unknown>) => {
      const current = config?.settings?.[name] ?? {};
      return save({ [name]: { ...current, ...patch } });
    },
    [config, save],
  );

  const runSpeakerTest = async () => {
    setBusy("speaker");
    setSpeaker(null);
    abort.current = new AbortController();
    setSpeaker(await testSpeaker(TEST_PHRASE, abort.current.signal));
    setBusy(null);
  };

  const runMicTest = async () => {
    setBusy("mic");
    setMic(null);
    abort.current = new AbortController();
    toast.info("Listening for 4 seconds — say something.");
    setMic(await testMicrophone(4, abort.current.signal));
    setBusy(null);
  };

  if (loadError) {
    return (
      <SettingsSection
        title="Voice"
        description="Talk to Nova, and Nova talks back."
      >
        <p className="text-destructive text-sm">{loadError}</p>
      </SettingsSection>
    );
  }
  if (!config) {
    return (
      <SettingsSection
        title="Voice"
        description="Talk to Nova, and Nova talks back."
      >
        <Loader2Icon className="text-muted-foreground size-4 animate-spin" />
      </SettingsSection>
    );
  }

  const settings = config.settings ?? {};
  const stt = settings.stt ?? {};
  const tts = settings.tts ?? {};
  const enabled = Boolean(settings.enabled);
  const ready = Boolean(status?.ready);
  const sttChoice =
    config.catalog.stt.find((c) => c.use === stt.use) ?? config.catalog.stt[0];
  const ttsChoice =
    config.catalog.tts.find((c) => c.use === tts.use) ?? config.catalog.tts[0];
  const turn = settings.turn ?? {};
  const turnChoice = config.catalog.turn?.[0];

  return (
    <SettingsSection
      title="Voice"
      description="Runs entirely on this machine — no API key, no per-minute cost, and no audio leaves the box."
    >
      <div className="space-y-6">
        {/* Live state. device_requested vs device_actual is the useful bit:
            `auto` resolves silently and `cuda` can fall back. */}
        <div
          className={cn(
            "rounded-lg border px-3 py-2.5 text-sm",
            enabled && ready
              ? "border-emerald-500/30 bg-emerald-500/5"
              : "border-amber-500/30 bg-amber-500/5",
          )}
        >
          <div className="flex items-center gap-2">
            {enabled && ready ? (
              <CheckCircle2Icon className="size-4 text-emerald-500" />
            ) : (
              <AlertTriangleIcon className="size-4 text-amber-500" />
            )}
            <span className="font-medium">
              {!enabled
                ? "Voice is off"
                : ready
                  ? "Voice is ready"
                  : "Voice is on, but the engines are not loading"}
            </span>
          </div>
          {status?.reason && (
            <p className="text-muted-foreground mt-1.5 font-mono text-xs">
              {status.reason}
            </p>
          )}
          {config.live && (
            <div className="text-muted-foreground mt-2 grid gap-1 text-xs sm:grid-cols-2">
              {(["stt", "tts"] as const).map((k) => {
                const live = config.live![k];
                const fellBack =
                  live.device_requested === "cuda" &&
                  live.device_actual !== "cuda";
                return (
                  <div key={k} className="flex items-center gap-1.5">
                    <span className="uppercase opacity-60">{k}</span>
                    <span className="font-medium">{live.name}</span>
                    {live.device_actual && (
                      <Badge
                        variant={
                          live.device_actual === "cuda"
                            ? "default"
                            : "secondary"
                        }
                        className="h-4 px-1.5 text-[10px]"
                      >
                        {live.device_actual}
                      </Badge>
                    )}
                    {fellBack && (
                      <span className="text-amber-500">
                        asked for GPU, running on CPU
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="divide-border/50 divide-y">
          <Row
            label="Enable voice"
            hint="Off by default. Turning this on loads the models, which takes a few seconds."
          >
            <Switch
              checked={enabled}
              disabled={saving}
              onCheckedChange={(v) => save({ enabled: v })}
            />
          </Row>
        </div>

        {/* ── Speech to text ─────────────────────────────────────────── */}
        <div>
          <h3 className="mb-1 text-sm font-semibold">Listening</h3>
          {sttChoice?.languages && (
            <p className="text-muted-foreground mb-2 text-xs">
              {sttChoice.languages}
            </p>
          )}
          <div className="divide-border/50 divide-y">
            <Row
              label="Model"
              hint="Bigger is more accurate and slower. On a GPU, accuracy becomes affordable."
            >
              <Choice
                options={(sttChoice?.models ?? []).map((m) => ({
                  value: m,
                  label: m,
                }))}
                value={stt.model ?? "base"}
                onChange={(v) => section("stt", { model: v })}
              />
            </Row>
            <Row
              label="Device"
              hint={
                DEVICES.find((d) => d.value === (stt.device ?? "auto"))?.hint
              }
            >
              <Choice
                options={DEVICES}
                value={stt.device ?? "auto"}
                onChange={(v) => section("stt", { device: v })}
              />
            </Row>
            <Row
              label="Language"
              hint="Blank auto-detects. Setting it explicitly is faster and more accurate."
            >
              <Input
                className="h-7 w-24 text-xs"
                placeholder="auto"
                defaultValue={stt.language ?? ""}
                onBlur={(e) => {
                  const v = e.target.value.trim();
                  if (v !== (stt.language ?? ""))
                    void section("stt", { language: v || null });
                }}
              />
            </Row>
          </div>
        </div>

        {/* ── Text to speech ─────────────────────────────────────────── */}
        <div>
          <h3 className="mb-1 text-sm font-semibold">Speaking</h3>
          {ttsChoice?.note && (
            <p className="text-muted-foreground mb-2 text-xs">
              {ttsChoice.note}
            </p>
          )}
          <div className="divide-border/50 divide-y">
            <Row label="Engine">
              <Choice
                options={config.catalog.tts.map((c) => ({
                  value: c.use,
                  label: c.label,
                }))}
                value={tts.use ?? ttsChoice?.use ?? ""}
                onChange={(v) => section("tts", { use: v })}
              />
            </Row>
            <Row label="Voice">
              <Choice
                options={(ttsChoice?.voices ?? [])
                  .slice(0, 4)
                  .map((v) => ({ value: v, label: v }))}
                value={tts.voice ?? "af_heart"}
                onChange={(v) => section("tts", { voice: v })}
              />
            </Row>
            <Row
              label="Device"
              hint={
                DEVICES.find((d) => d.value === (tts.device ?? "auto"))?.hint
              }
            >
              <Choice
                options={DEVICES}
                value={tts.device ?? "auto"}
                onChange={(v) => section("tts", { device: v })}
              />
            </Row>
          </div>
        </div>

        {/* ── Turn-taking ────────────────────────────────────────────── */}
        <div>
          <h3 className="mb-1 text-sm font-semibold">Turn-taking</h3>
          <p className="text-muted-foreground mb-2 text-xs">
            {turnChoice?.note}
          </p>
          <div className="divide-border/50 divide-y">
            <Row
              label="Wait while you think"
              hint="Off by default. Judges whether you finished a thought rather than just stopping — verify it with your own voice using the microphone test below."
            >
              <Switch
                checked={Boolean(turn.enabled)}
                disabled={saving || !enabled}
                onCheckedChange={(v) =>
                  section("turn", { enabled: v, use: turnChoice?.use })
                }
              />
            </Row>
            {Boolean(turn.enabled) && (
              <Row
                label="Confidence"
                hint="Higher means Nova waits more readily. A wrong wait costs you real time, so this is not free."
              >
                <Choice
                  options={[
                    { value: "0.5", label: "0.5" },
                    { value: "0.7", label: "0.7" },
                    { value: "0.85", label: "0.85" },
                  ]}
                  value={String(turn.threshold ?? 0.7)}
                  onChange={(v) => section("turn", { threshold: Number(v) })}
                />
              </Row>
            )}
          </div>
        </div>

        {/* ── The lab: drive the real engines ────────────────────────── */}
        <VoiceLab
          enabled={enabled}
          voices={ttsChoice?.voices ?? []}
          activeVoice={tts.voice ?? "af_heart"}
          onPickVoice={(v) => section("tts", { voice: v })}
        />

        {/* ── Quick tests ────────────────────────────────────────────── */}
        <div>
          <h3 className="mb-1 text-sm font-semibold">One-click checks</h3>
          <p className="text-muted-foreground mb-3 text-xs">
            The same paths as the lab above, with fixed inputs — for when you
            just want a yes or no.
          </p>

          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <Button
                size="sm"
                variant="outline"
                onClick={runSpeakerTest}
                disabled={!enabled || busy !== null}
              >
                {busy === "speaker" ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <Volume2Icon className="size-3.5" />
                )}
                Test speaker
              </Button>
              {speaker && (
                <span
                  className={cn(
                    "text-xs",
                    speaker.ok ? "text-muted-foreground" : "text-destructive",
                  )}
                >
                  {speaker.ok ? (
                    <>
                      {speaker.blocked && (
                        <>
                          <span className="text-amber-500">
                            browser blocked autoplay
                          </span>
                          {" · "}
                        </>
                      )}
                      {Math.round(speaker.latencyMs)} ms
                      {speaker.rtf != null && (
                        <>
                          {" · "}
                          <span
                            className={
                              speaker.rtf > 1 ? "text-amber-500" : undefined
                            }
                          >
                            {speaker.rtf.toFixed(2)}× real time
                            {speaker.rtf > 1 && " — slower than playback"}
                          </span>
                        </>
                      )}
                    </>
                  ) : (
                    speaker.error
                  )}
                </span>
              )}
            </div>

            <div className="flex items-center gap-3">
              <Button
                size="sm"
                variant="outline"
                onClick={runMicTest}
                disabled={!enabled || busy !== null}
              >
                {busy === "mic" ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <MicIcon className="size-3.5" />
                )}
                Test microphone
              </Button>
              {mic && (
                <span
                  className={cn(
                    "text-xs",
                    mic.ok ? "text-muted-foreground" : "text-destructive",
                  )}
                >
                  {mic.ok
                    ? mic.heard
                      ? `heard: “${mic.heard}”`
                      : "recorded, but no words were recognised"
                    : mic.error}
                </span>
              )}
            </div>
          </div>
        </div>

        {config.has_overrides && (
          <div className="border-panel-border flex items-center justify-between gap-4 border-t pt-4">
            <p className="text-muted-foreground text-xs">
              These settings are saved separately from{" "}
              <code className="font-mono">config.yaml</code>, which is never
              modified.
            </p>
            <Button
              size="sm"
              variant="ghost"
              disabled={saving}
              onClick={async () => {
                setConfig(await resetVoiceConfig());
                setStatus(await getVoiceStatus(true));
                toast.success("Reverted to config.yaml");
              }}
            >
              <RotateCcwIcon className="size-3.5" />
              Reset
            </Button>
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
