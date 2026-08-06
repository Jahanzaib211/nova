"use client";

/**
 * Mic button + live voice status.
 *
 * Renders nothing when voice is not configured, so an operator who never
 * enabled it sees no dead control — better than a button that fails on click.
 */

import { LoaderCircleIcon, MicIcon, MicOffIcon, Volume2Icon } from "lucide-react";

import { useVoiceSession } from "@/core/voice/session";
import { isLive, phaseLabel } from "@/core/voice/state";
import { cn } from "@/lib/utils";

import { PromptInputButton } from "../ai-elements/prompt-input";

import { Tooltip } from "./tooltip";

export function VoiceButton({
  threadId,
  className,
}: {
  threadId: string;
  className?: string;
}) {
  const { state, available, start, stop } = useVoiceSession(threadId);

  if (!available) return null;

  const live = isLive(state);
  const label = phaseLabel(state);

  const icon = (() => {
    if (state.phase === "connecting") {
      return <LoaderCircleIcon className="size-3 animate-spin" />;
    }
    if (state.phase === "speaking") {
      return <Volume2Icon className="size-3 text-emerald-400" />;
    }
    if (state.phase === "listening") {
      return <MicIcon className="size-3 animate-pulse text-red-400" />;
    }
    if (live) return <MicIcon className="size-3 text-emerald-400" />;
    return <MicOffIcon className="size-3" />;
  })();

  return (
    <Tooltip content={live ? `${label} — click to stop` : "Talk to Nova"}>
      <PromptInputButton
        type="button"
        aria-label={live ? "Stop voice session" : "Start voice session"}
        aria-pressed={live}
        data-testid="voice-button"
        data-voice-phase={state.phase}
        className={cn("px-2!", className)}
        onClick={() => (live ? stop() : void start())}
      >
        {icon}
      </PromptInputButton>
    </Tooltip>
  );
}
