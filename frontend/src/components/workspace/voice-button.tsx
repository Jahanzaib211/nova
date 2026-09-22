"use client";

/**
 * Voice mode: the mic control plus the live session panel.
 *
 * Both live here because they share one `useVoiceSession` — the hook owns the
 * socket, and mounting it twice would open two.
 *
 * The control is **always rendered**. An earlier version returned `null` when
 * voice wasn't configured, which made the whole feature invisible: nobody can
 * discover a capability that leaves no trace in the UI. When it isn't ready the
 * pill still shows, explains itself, and says how to turn it on.
 */

import { MicIcon, SparklesIcon, SquareIcon } from "lucide-react";
import { useState } from "react";

import { useVoiceSession } from "@/core/voice/session";
import { isLive, phaseLabel } from "@/core/voice/state";
import { cn } from "@/lib/utils";

import { Tooltip } from "./tooltip";
import { VOICE_KEYFRAMES, VoiceOrb } from "./voice-orb";

export function VoiceButton({
  threadId,
  className,
}: {
  threadId: string;
  className?: string;
}) {
  const { state, available, start, stop } = useVoiceSession(threadId);
  const [hint, setHint] = useState(false);

  const live = isLive(state);

  return (
    <>
      <style>{VOICE_KEYFRAMES}</style>

      {live && <VoicePanel state={state} onStop={stop} />}
      {hint && !available && !live && (
        <VoiceSetupHint onClose={() => setHint(false)} />
      )}

      <Tooltip
        content={
          live
            ? `${phaseLabel(state)} — click to end`
            : available
              ? "Talk to Nova — she talks back"
              : "Voice is available — click to see how to enable it"
        }
      >
        <button
          type="button"
          aria-label={live ? "End voice session" : "Start voice session"}
          aria-pressed={live}
          data-testid="voice-button"
          data-voice-phase={state.phase}
          data-voice-available={available ? "true" : "false"}
          onClick={() => {
            if (live) return stop();
            if (!available) return setHint((v) => !v);
            void start();
          }}
          className={cn(
            // A labelled pill, not a bare icon. The whole point is that someone
            // who has never used voice can see the feature exists.
            "relative inline-flex h-7 shrink-0 items-center gap-1.5 overflow-hidden rounded-full border px-2.5 text-[11px] font-medium transition-all",
            live
              ? "border-sky-400/50 bg-gradient-to-r from-sky-500/20 to-indigo-500/20 text-sky-300"
              : "border-sky-400/30 bg-gradient-to-r from-sky-500/10 to-indigo-500/10 text-sky-400/90 hover:border-sky-400/60 hover:text-sky-300",
            className,
          )}
          style={
            live
              ? undefined
              : // Idle glow: enough motion to catch the eye once, not enough to
                // nag. Disabled entirely for reduced-motion users below.
                { animation: "nova-voice-glow 3.4s ease-in-out infinite" }
          }
        >
          {/* Shimmer sweep — the "this is new, look here" cue. */}
          {!live && (
            <span
              aria-hidden
              className="pointer-events-none absolute inset-y-0 -left-1/3 w-1/3 skew-x-[-20deg] bg-gradient-to-r from-transparent via-white/25 to-transparent motion-reduce:hidden"
              style={{
                animation: "nova-voice-shimmer 3.4s ease-in-out infinite",
              }}
            />
          )}

          {live ? (
            <VoiceOrb phase={state.phase} size={16} />
          ) : (
            <MicIcon className="size-3.5" />
          )}
          <span className="relative">{live ? phaseLabel(state) : "Voice"}</span>
          {!live && <SparklesIcon className="relative size-2.5 opacity-70" />}
        </button>
      </Tooltip>
    </>
  );
}

/** Shown when someone clicks Voice before it has been switched on. */
function VoiceSetupHint({ onClose }: { onClose: () => void }) {
  return (
    <div
      data-testid="voice-setup-hint"
      role="status"
      className="absolute bottom-full left-0 z-20 mb-2 w-full px-1"
    >
      <div className="border-panel-border bg-background/95 rounded-xl border px-3 py-2.5 text-[11px] shadow-xl backdrop-blur">
        <div className="flex items-start gap-2">
          <MicIcon className="mt-0.5 size-3.5 shrink-0 text-sky-400" />
          <div className="min-w-0 flex-1">
            <p className="text-foreground font-medium">
              Voice is built in — it just needs switching on
            </p>
            <p className="text-muted-foreground mt-1">
              Runs entirely on this machine. No API key, no per-minute cost, and
              no audio leaves the box.
            </p>
            <pre className="text-muted-foreground/80 bg-muted/40 mt-1.5 overflow-x-auto rounded-md px-2 py-1.5 font-mono text-[10px] leading-relaxed">
              {`cd backend && uv sync --extra voice
./scripts/fetch-voice-models.sh
# then set  speech.enabled: true  in config.yaml`}
            </pre>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Dismiss"
            className="text-muted-foreground/60 hover:text-foreground shrink-0"
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * The live session card, floated above the composer.
 *
 * `absolute` + `bottom-full` keeps it out of the composer's flex flow, so
 * turning voice on doesn't reflow the textarea under the user's cursor.
 */
function VoicePanel({
  state,
  onStop,
}: {
  state: ReturnType<typeof useVoiceSession>["state"];
  onStop: () => void;
}) {
  const { phase, transcript, assistant, error } = state;

  const accent =
    phase === "listening"
      ? "text-rose-300"
      : phase === "speaking"
        ? "text-emerald-300"
        : phase === "thinking"
          ? "text-amber-300"
          : "text-sky-300";

  return (
    <div
      data-testid="voice-panel"
      data-voice-phase={phase}
      role="status"
      aria-live="polite"
      className="absolute bottom-full left-0 z-20 mb-2 w-full px-1"
    >
      <div className="border-panel-border bg-background/95 flex items-center gap-3 rounded-xl border px-3 py-2.5 shadow-xl backdrop-blur">
        <VoiceOrb phase={phase} size={40} />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={cn("text-xs font-semibold", accent)}>
              {phaseLabel(state)}
            </span>
            {phase === "speaking" && (
              // The single most useful thing to tell someone in voice mode.
              <span className="text-muted-foreground/60 text-[10px]">
                just start talking to interrupt
              </span>
            )}
          </div>

          <p className="text-muted-foreground mt-0.5 truncate text-[11px]">
            {error ??
              (phase === "speaking" && assistant
                ? assistant
                : transcript || "Listening for your voice…")}
          </p>
        </div>

        <button
          type="button"
          onClick={onStop}
          aria-label="End voice session"
          data-testid="voice-stop"
          className="shrink-0 rounded-lg border border-rose-400/40 bg-rose-500/10 p-1.5 text-rose-400 transition-colors hover:bg-rose-500/20 hover:text-rose-300"
        >
          <SquareIcon className="size-3 fill-current" />
        </button>
      </div>
    </div>
  );
}
