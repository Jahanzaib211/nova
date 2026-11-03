"use client";

import { PauseIcon, PlayIcon, Trash2Icon, VideoIcon } from "lucide-react";
import { useEffect, useState } from "react";

import {
  describeAction,
  frameForStep,
  recordingFrameUrl,
  stepForFrame,
  useRecordingManifest,
  useRecordings,
  useThreadBrowserState,
} from "@/core/browser/api";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

/**
 * Replay of recorded browser sessions: a frame scrubber plus the step list
 * (one row per browser_* call), kept in sync both ways. Frames and manifests
 * come from owner-checked, same-origin endpoints; manifests carry no typed
 * text, scripts, cookies or credential-looking query values.
 */
export function BrowserReplay({
  threadId,
  active,
}: {
  threadId: string;
  active: boolean;
}) {
  const { t } = useI18n();
  const tb = t.features.browser;
  const state = useThreadBrowserState(threadId, active);
  const { data: list, remove } = useRecordings(threadId, active);
  const sessions = list?.recordings ?? [];
  const [sessionId, setSessionId] = useState<string | null>(null);
  const selected = sessionId ?? sessions[0]?.session_id ?? null;
  const { data: manifest } = useRecordingManifest(
    active ? threadId : null,
    selected,
  );
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const frameCount = manifest?.frames.length ?? 0;

  useEffect(() => {
    setFrame(0);
    setPlaying(false);
  }, [selected]);

  useEffect(() => {
    if (!playing || !active || frameCount === 0) return;
    const fps = Math.max(1, Math.min(manifest?.fps ?? 2, 10));
    const id = window.setInterval(() => {
      setFrame((f) => {
        if (f + 1 >= frameCount) {
          setPlaying(false);
          return f;
        }
        return f + 1;
      });
    }, 1000 / fps);
    return () => window.clearInterval(id);
  }, [playing, active, frameCount, manifest?.fps]);

  const recordingOn = state.data?.recording ?? false;
  const currentStep = manifest ? stepForFrame(manifest.actions, frame) : -1;
  const currentFrame = manifest?.frames[frame];

  return (
    <section
      className="border-panel-border border-t px-3 py-2"
      data-testid="browser-replay"
    >
      <div className="flex items-center gap-2">
        <VideoIcon className="h-3.5 w-3.5" />
        <span className="text-xs font-medium">{tb.recordingTitle}</span>
        <label
          className="text-muted-foreground ml-auto flex items-center gap-1 text-[11px]"
          title={tb.recordingCost}
        >
          <input
            type="checkbox"
            checked={recordingOn}
            onChange={(e) => void state.setRecording(e.target.checked)}
          />
          {recordingOn ? tb.recordingOn : tb.recordingOff}
        </label>
      </div>
      {sessions.length === 0 ? (
        <p className="text-muted-foreground mt-1 text-[11px]">
          {tb.recordingsEmpty}
        </p>
      ) : (
        <div className="mt-2 space-y-2">
          <div className="flex items-center gap-2">
            <select
              className="bg-background min-w-0 flex-1 rounded border px-1 py-0.5 text-[11px]"
              value={selected ?? ""}
              onChange={(e) => setSessionId(e.target.value)}
            >
              {sessions.map((s) => (
                <option key={s.session_id} value={s.session_id}>
                  {new Date(s.started_at * 1000).toLocaleString()} ·{" "}
                  {s.action_count} steps
                </option>
              ))}
            </select>
            {selected && (
              <button
                type="button"
                title={tb.deleteRecording}
                onClick={() => {
                  void remove(selected);
                  setSessionId(null);
                }}
                className="text-muted-foreground hover:text-foreground"
              >
                <Trash2Icon className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          {manifest && frameCount > 0 && currentFrame && (
            <>
              <img
                src={recordingFrameUrl(
                  threadId,
                  manifest.session_id,
                  currentFrame.file,
                )}
                alt={`${frame + 1}/${frameCount}`}
                className="w-full rounded border bg-black object-contain"
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  aria-label={playing ? tb.pause : tb.play}
                  onClick={() => setPlaying((p) => !p)}
                >
                  {playing ? (
                    <PauseIcon className="h-3.5 w-3.5" />
                  ) : (
                    <PlayIcon className="h-3.5 w-3.5" />
                  )}
                </button>
                <input
                  type="range"
                  min={0}
                  max={frameCount - 1}
                  value={frame}
                  onChange={(e) => {
                    setPlaying(false);
                    setFrame(Number(e.target.value));
                  }}
                  className="flex-1"
                  aria-label="scrubber"
                />
                <span className="text-muted-foreground font-mono text-[10px]">
                  {frame + 1}/{frameCount}
                </span>
              </div>
              {manifest.truncated && (
                <p className="text-warning text-[10px]">{tb.truncated}</p>
              )}
            </>
          )}
          {manifest && manifest.actions.length > 0 && (
            <ol
              className="max-h-40 space-y-0.5 overflow-auto text-[11px]"
              aria-label={tb.steps}
            >
              {manifest.actions.map((a, i) => (
                <li key={`${a.t}-${i}`}>
                  <button
                    type="button"
                    onClick={() => {
                      setPlaying(false);
                      setFrame(frameForStep(manifest.actions, i, frameCount));
                    }}
                    className={cn(
                      "w-full truncate rounded px-1 text-left",
                      i === currentStep
                        ? "bg-muted font-medium"
                        : "hover:bg-muted/50",
                      !a.ok && "text-destructive",
                    )}
                  >
                    <span className="text-muted-foreground font-mono">
                      {a.t.toFixed(1)}s
                    </span>{" "}
                    {describeAction(a)}
                  </button>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}
