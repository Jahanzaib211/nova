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
 * Labels, hints, states and toasts come from `t.features.voice`; model names,
 * device ids and measured numbers are shown verbatim on purpose — a
 * translated model file name is worse than an untranslated one.
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
import { SettingsSection } from "@/components/workspace/settings/settings-section";
import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";
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

import { VoiceLab } from "../components/voice-lab";

type VoiceCopy = Translations["features"]["voice"];

/** Devices offered per engine. `auto` is the only one safe on every host. */
function devices(v: VoiceCopy) {
  return [
    { value: "auto", label: v.devices.auto, hint: v.devices.autoHint },
    { value: "cuda", label: v.devices.cuda, hint: v.devices.cudaHint },
    { value: "cpu", label: v.devices.cpu, hint: v.devices.cpuHint },
  ];
}

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
  const { t } = useI18n();
  const v = t.features.voice;
  const DEVICES = devices(v);
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
        toast.success(v.toasts.saved);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : v.toasts.saveFailed);
      } finally {
        setSaving(false);
      }
    },
    [config, v.toasts.saveFailed, v.toasts.saved],
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
    setSpeaker(await testSpeaker(v.testPhrase, abort.current.signal));
    setBusy(null);
  };

  const runMicTest = async () => {
    setBusy("mic");
    setMic(null);
    abort.current = new AbortController();
    toast.info(v.toasts.listening);
    setMic(await testMicrophone(4, abort.current.signal));
    setBusy(null);
  };

  if (loadError) {
    return (
      <SettingsSection title={v.title} description={v.tagline}>
        <p className="text-destructive text-sm">{loadError}</p>
      </SettingsSection>
    );
  }
  if (!config) {
    return (
      <SettingsSection title={v.title} description={v.tagline}>
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
    <SettingsSection title={v.title} description={v.description}>
      <div className="space-y-6">
        {/* Live state. device_requested vs device_actual is the useful bit:
            `auto` resolves silently and `cuda` can fall back. */}
        <div
          className={cn(
            "rounded-lg border px-3 py-2.5 text-sm",
            enabled && ready
              ? "border-success/30 bg-success/5"
              : "border-warning/30 bg-warning/5",
          )}
        >
          <div className="flex items-center gap-2">
            {enabled && ready ? (
              <CheckCircle2Icon className="text-success size-4" />
            ) : (
              <AlertTriangleIcon className="text-warning size-4" />
            )}
            <span className="font-medium">
              {!enabled
                ? v.state.off
                : ready
                  ? v.state.ready
                  : v.state.notLoading}
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
                      <span className="text-warning">{v.state.fellBack}</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="divide-border/50 divide-y">
          <Row label={v.enable} hint={v.enableHint}>
            <Switch
              checked={enabled}
              disabled={saving}
              onCheckedChange={(v) => save({ enabled: v })}
            />
          </Row>
        </div>

        {/* ── Speech to text ─────────────────────────────────────────── */}
        <div>
          <h3 className="mb-1 text-sm font-semibold">{v.listening}</h3>
          {sttChoice?.languages && (
            <p className="text-muted-foreground mb-2 text-xs">
              {sttChoice.languages}
            </p>
          )}
          <div className="divide-border/50 divide-y">
            <Row label={v.model} hint={v.modelHint}>
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
              label={v.device}
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
            <Row label={v.language} hint={v.languageHint}>
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
          <h3 className="mb-1 text-sm font-semibold">{v.speaking}</h3>
          {ttsChoice?.note && (
            <p className="text-muted-foreground mb-2 text-xs">
              {ttsChoice.note}
            </p>
          )}
          <div className="divide-border/50 divide-y">
            <Row label={v.engine}>
              <Choice
                options={config.catalog.tts.map((c) => ({
                  value: c.use,
                  label: c.label,
                }))}
                value={tts.use ?? ttsChoice?.use ?? ""}
                onChange={(v) => section("tts", { use: v })}
              />
            </Row>
            <Row label={v.voice}>
              <Choice
                options={(ttsChoice?.voices ?? [])
                  .slice(0, 4)
                  .map((v) => ({ value: v, label: v }))}
                value={tts.voice ?? "af_heart"}
                onChange={(v) => section("tts", { voice: v })}
              />
            </Row>
            <Row
              label={v.device}
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
          <h3 className="mb-1 text-sm font-semibold">{v.turnTaking}</h3>
          <p className="text-muted-foreground mb-2 text-xs">
            {turnChoice?.note}
          </p>
          <div className="divide-border/50 divide-y">
            <Row label={v.waitWhileThinking} hint={v.waitWhileThinkingHint}>
              <Switch
                checked={Boolean(turn.enabled)}
                disabled={saving || !enabled}
                onCheckedChange={(v) =>
                  section("turn", { enabled: v, use: turnChoice?.use })
                }
              />
            </Row>
            {Boolean(turn.enabled) && (
              <Row label={v.confidence} hint={v.confidenceHint}>
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
          <h3 className="mb-1 text-sm font-semibold">{v.checks.title}</h3>
          <p className="text-muted-foreground mb-3 text-xs">
            {v.checks.description}
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
                {v.checks.testSpeaker}
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
                          <span className="text-warning">
                            {v.checks.autoplayBlocked}
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
                              speaker.rtf > 1 ? "text-warning" : undefined
                            }
                          >
                            {v.checks.realTime(speaker.rtf.toFixed(2))}
                            {speaker.rtf > 1 && v.checks.slowerThanPlayback}
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
                {v.checks.testMicrophone}
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
                      ? v.checks.heard(mic.heard)
                      : v.checks.nothingRecognised
                    : mic.error}
                </span>
              )}
            </div>
          </div>
        </div>

        {config.has_overrides && (
          <div className="border-panel-border flex items-center justify-between gap-4 border-t pt-4">
            <p className="text-muted-foreground text-xs">{v.overridesNote}</p>
            <Button
              size="sm"
              variant="ghost"
              disabled={saving}
              onClick={async () => {
                setConfig(await resetVoiceConfig());
                setStatus(await getVoiceStatus(true));
                toast.success(v.toasts.reverted);
              }}
            >
              <RotateCcwIcon className="size-3.5" />
              {v.reset}
            </Button>
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
