"use client";

/**
 * The visual identity of voice mode.
 *
 * Each phase gets a distinct motion so the state is readable at a glance,
 * without reading a word:
 *   listening — rings pushing outward, as if the mic is drawing you in
 *   thinking  — a single ring orbiting, the classic "working" idiom
 *   speaking  — equaliser bars, the universal sign that audio is coming out
 *   idle      — a slow breath, so a live session never looks frozen
 *
 * Animation is inline-keyframe CSS rather than Tailwind utilities because the
 * bars need individually staggered delays, which utility classes cannot express.
 */

import type { VoicePhase } from "@/core/voice/state";
import { cn } from "@/lib/utils";

export const VOICE_KEYFRAMES = `
@keyframes nova-voice-ripple {
  0%   { transform: scale(0.85); opacity: 0.55; }
  100% { transform: scale(1.9);  opacity: 0; }
}
@keyframes nova-voice-breathe {
  0%, 100% { transform: scale(1);    opacity: 0.85; }
  50%      { transform: scale(1.07); opacity: 1; }
}
@keyframes nova-voice-bar {
  0%, 100% { transform: scaleY(0.28); }
  50%      { transform: scaleY(1); }
}
@keyframes nova-voice-shimmer {
  0%   { transform: translateX(-120%); }
  60%, 100% { transform: translateX(220%); }
}
@keyframes nova-voice-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(56,189,248,0.35); }
  50%      { box-shadow: 0 0 14px 3px rgba(56,189,248,0.28); }
}
@keyframes nova-voice-orbit {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
`;

/** Bar heights chosen so the equaliser reads as speech, not a loading spinner. */
const BARS = [0.5, 0.9, 0.65, 1, 0.55];

export function VoiceOrb({
  phase,
  size = 36,
  className,
}: {
  phase: VoicePhase;
  size?: number;
  className?: string;
}) {
  const listening = phase === "listening";
  const speaking = phase === "speaking";
  const thinking = phase === "thinking";
  const live = listening || speaking || thinking || phase === "idle";

  const tint = listening
    ? "from-rose-400/90 to-red-500/90"
    : speaking
      ? "from-emerald-300/90 to-teal-500/90"
      : thinking
        ? "from-amber-300/90 to-orange-500/90"
        : "from-sky-300/80 to-indigo-500/80";

  return (
    <span
      className={cn(
        "relative inline-flex shrink-0 items-center justify-center",
        className,
      )}
      style={{ width: size, height: size }}
      aria-hidden
    >
      <style>{VOICE_KEYFRAMES}</style>

      {/* Ripples — only while listening, so they mean "I can hear you". */}
      {listening &&
        [0, 0.6].map((delay) => (
          <span
            key={delay}
            className="absolute inset-0 rounded-full border border-rose-400/70"
            style={{
              animation: `nova-voice-ripple 1.6s ease-out ${delay}s infinite`,
            }}
          />
        ))}

      {/* Orbiting arc while thinking. */}
      {thinking && (
        <span
          className="absolute inset-[-3px] rounded-full border-2 border-transparent border-t-amber-300"
          style={{ animation: "nova-voice-orbit 1.1s linear infinite" }}
        />
      )}

      {/* Core. */}
      <span
        className={cn(
          "relative flex items-center justify-center rounded-full bg-gradient-to-br shadow-lg",
          tint,
        )}
        style={{
          width: size * 0.72,
          height: size * 0.72,
          animation:
            live && !speaking
              ? "nova-voice-breathe 2.6s ease-in-out infinite"
              : undefined,
        }}
      >
        {speaking ? (
          <span
            className="flex items-end gap-[2px]"
            style={{ height: size * 0.36 }}
          >
            {BARS.map((scale, i) => (
              <span
                key={i}
                className="w-[2px] rounded-full bg-white/95"
                style={{
                  height: `${scale * 100}%`,
                  transformOrigin: "bottom",
                  animation: `nova-voice-bar 0.6s ease-in-out ${i * 0.09}s infinite`,
                }}
              />
            ))}
          </span>
        ) : (
          <span
            className="rounded-full bg-white/90"
            style={{ width: size * 0.18, height: size * 0.18 }}
          />
        )}
      </span>
    </span>
  );
}
