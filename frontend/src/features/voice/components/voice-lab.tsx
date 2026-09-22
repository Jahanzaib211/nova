"use client";

/**
 * The Voice Lab — a bench for actually driving the voice stack, not just
 * configuring it.
 *
 * The settings above it answer "what is voice set to?". This answers the two
 * questions you have when something feels wrong: **does it work, and how fast?**
 * Voice depends on server-side weights, an optional GPU and microphone
 * permission, so a setting can be valid and still produce silence. Everything
 * here runs the real path and reports measured numbers.
 *
 * Kept in its own component because it holds a lot of transient state (per-voice
 * previews, a benchmark table, recordings) that the settings rows do not care
 * about, and mixing them made the page hard to follow.
 */

import {
  GaugeIcon,
  Loader2Icon,
  MicIcon,
  PlayIcon,
  SquareIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import {
  stopSpeaking,
  testMicrophone,
  testSpeaker,
  type MicTest,
  type SpeakerTest,
} from "@/core/voice/config";
import { cn } from "@/lib/utils";

type VoiceSample = { voice: string; result: SpeakerTest };

function Metric({
  label,
  value,
  warn,
}: {
  label: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground text-[10px] tracking-wide uppercase">
        {label}
      </div>
      <div className={cn("font-mono text-sm", warn && "text-warning")}>
        {value}
      </div>
    </div>
  );
}

export function VoiceLab({
  enabled,
  voices,
  activeVoice,
  onPickVoice,
}: {
  enabled: boolean;
  voices: string[];
  activeVoice: string;
  onPickVoice: (voice: string) => void;
}) {
  const { t } = useI18n();
  const l = t.features.voice.lab;
  // Long enough to be a fair timing sample, short enough not to be a wait.
  const [text, setText] = useState(l.defaultText);
  const [speaking, setSpeaking] = useState(false);
  const [last, setLast] = useState<SpeakerTest | null>(null);
  const [samples, setSamples] = useState<VoiceSample[]>([]);
  const [benching, setBenching] = useState(false);
  const [recording, setRecording] = useState(false);
  const [heard, setHeard] = useState<MicTest | null>(null);
  const abort = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    // Aborting the fetch does nothing to audio that is already playing —
    // the clip would keep talking after the button said it stopped.
    stopSpeaking();
    setSpeaking(false);
    setBenching(false);
    setRecording(false);
  }, []);

  // Leaving the panel (or the dialog closing) must not leave a voice talking
  // to an empty room.
  useEffect(
    () => () => {
      abort.current?.abort();
      stopSpeaking();
    },
    [],
  );

  const speak = useCallback(async () => {
    if (!text.trim()) return;
    setSpeaking(true);
    setLast(null);
    abort.current = new AbortController();
    setLast(
      await testSpeaker(text, abort.current.signal, { awaitPlayback: true }),
    );
    setSpeaking(false);
  }, [text]);

  /**
   * Speak the same sentence in every voice, sequentially.
   *
   * Sequential on purpose: the engine is a single process-wide session, so
   * parallel requests would queue behind each other anyway and the timings
   * would measure contention rather than synthesis.
   */
  const auditionAll = useCallback(async () => {
    setBenching(true);
    setSamples([]);
    abort.current = new AbortController();
    const signal = abort.current.signal;
    for (const voice of voices) {
      if (signal.aborted) break;
      // awaitPlayback is what makes this an audition rather than a pile-up:
      // play() resolves when audio *starts*, so without it all eight voices
      // speak at once.
      const result = await testSpeaker(
        `This is ${voice.replace(/^[abm]{1,2}_/, "")}. ${text}`,
        signal,
        { awaitPlayback: true },
      );
      setSamples((prev) => [...prev, { voice, result }]);
    }
    setBenching(false);
  }, [voices, text]);

  const listen = useCallback(async () => {
    setRecording(true);
    setHeard(null);
    abort.current = new AbortController();
    setHeard(await testMicrophone(5, abort.current.signal));
    setRecording(false);
  }, []);

  const busy = speaking || benching || recording;

  return (
    <div className="border-panel-border rounded-lg border p-4">
      <div className="mb-1 flex items-center gap-2">
        <GaugeIcon className="text-info size-4" />
        <h3 className="text-sm font-semibold">{l.title}</h3>
      </div>
      <p className="text-muted-foreground mb-3 text-xs">{l.description}</p>

      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        placeholder={l.placeholder}
        className="mb-3 text-sm"
        disabled={!enabled}
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          onClick={speak}
          disabled={!enabled || busy || !text.trim()}
        >
          {speaking ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <PlayIcon className="size-3.5" />
          )}
          {l.speakIt}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={auditionAll}
          disabled={!enabled || busy || voices.length === 0}
        >
          {benching ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <GaugeIcon className="size-3.5" />
          )}
          {l.auditionAll}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={listen}
          disabled={!enabled || busy}
        >
          {recording ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <MicIcon className="size-3.5" />
          )}
          {l.speakToNova}
        </Button>
        {busy && (
          <Button size="sm" variant="ghost" onClick={stop}>
            <SquareIcon className="size-3 fill-current" />
            {l.stop}
          </Button>
        )}
      </div>

      {last && (
        <div className="bg-muted/30 mb-3 rounded-md px-3 py-2">
          {last.ok ? (
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <Metric
                label={l.firstAudio}
                value={`${Math.round(last.latencyMs)} ms`}
                warn={last.latencyMs > 1000}
              />
              <Metric
                label={l.audioLength}
                value={last.durationS ? `${last.durationS.toFixed(2)} s` : "—"}
              />
              {/* RTF above 1 means synthesis is slower than playback — it
                  stutters rather than failing, so naming it matters. */}
              <Metric
                label={l.realTimeFactor}
                value={last.rtf != null ? `${last.rtf.toFixed(2)}×` : "—"}
                warn={(last.rtf ?? 0) > 1}
              />
              {last.blocked && (
                <div className="text-warning flex w-full items-center gap-2 text-xs">
                  <span>{l.autoplayBlocked}</span>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-xs"
                    onClick={() => void last.play?.()}
                  >
                    {l.playIt}
                  </Button>
                </div>
              )}
              {(last.rtf ?? 0) > 1 && (
                <p className="text-warning text-xs">{l.slowerThanRealTime}</p>
              )}
            </div>
          ) : (
            <p className="text-destructive text-xs">{last.error}</p>
          )}
        </div>
      )}

      {heard && (
        <div className="bg-muted/30 mb-3 rounded-md px-3 py-2 text-xs">
          {heard.ok ? (
            heard.heard ? (
              <>
                <span className="text-muted-foreground">{l.novaHeard} </span>
                <span className="font-medium">“{heard.heard}”</span>
              </>
            ) : (
              <span className="text-warning">
                {l.nothingRecognised(heard.durationS ?? 0)}
              </span>
            )
          ) : (
            <span className="text-destructive">{heard.error}</span>
          )}
        </div>
      )}

      {samples.length > 0 && (
        <div className="space-y-1">
          <div className="text-muted-foreground text-[10px] tracking-wide uppercase">
            {l.voicesHint}
          </div>
          {samples.map(({ voice, result }) => (
            <button
              key={voice}
              type="button"
              onClick={() => onPickVoice(voice)}
              className={cn(
                "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                voice === activeVoice
                  ? "bg-info/10 text-info"
                  : "hover:bg-muted/50",
              )}
            >
              <span className="font-mono">{voice}</span>
              <span className="text-muted-foreground">
                {result.ok
                  ? `${Math.round(result.latencyMs)} ms · ${result.rtf?.toFixed(2) ?? "—"}×`
                  : l.failed}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
