// Browser layer API: saved profiles (per user), per-thread browser state
// (captcha hand-off, recording toggle) and session recordings.
// Backend: app/gateway/routers/browser_layer.py.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export interface BrowserProfile {
  name: string;
  domains: string[];
  cookie_count: number;
  origin_count: number;
  bytes: number;
  created_at: number | null;
  updated_at: number | null;
  is_default: boolean;
  corrupt: boolean;
}

export interface BrowserAttention {
  kind: string;
  url: string;
  title?: string;
  reason?: string;
  at: number;
  resolved_at: number | null;
}

export interface ThreadBrowserState {
  recording: boolean;
  recording_default: boolean;
  profile: string | null;
  attention: BrowserAttention | null;
}

export interface RecordingSummary {
  session_id: string;
  started_at: number;
  updated_at: number;
  frame_count: number;
  action_count: number;
  bytes: number;
  truncated: boolean;
}

export interface RecordingFrame {
  file: string;
  t: number;
}

export interface RecordingAction {
  tool: string;
  t: number;
  ok: boolean;
  frame: number | null;
  url?: string;
  page_url?: string;
  selector?: string;
  text_chars?: number;
  script_chars?: number;
  error?: string;
}

export interface RecordingManifest extends RecordingSummary {
  fps: number;
  frames: RecordingFrame[];
  actions: RecordingAction[];
}

const base = () => getBackendBaseURL();
const threadPath = (threadId: string, rest: string) =>
  `${base()}/api/threads/${encodeURIComponent(threadId)}/browser${rest}`;

export function recordingFrameUrl(
  threadId: string,
  sessionId: string,
  frame: string,
): string {
  return threadPath(
    threadId,
    `/recordings/${encodeURIComponent(sessionId)}/frames/${encodeURIComponent(frame)}`,
  );
}

/** Index of the step whose frame is the latest one at or before `frame`. */
export function stepForFrame(
  actions: RecordingAction[],
  frame: number,
): number {
  let best = -1;
  actions.forEach((a, i) => {
    if (a.frame !== null && a.frame <= frame) best = i;
  });
  return best;
}

/** Frame to show for a step: its own still, else the nearest earlier frame. */
export function frameForStep(
  actions: RecordingAction[],
  step: number,
  frameCount: number,
): number {
  for (let i = step; i >= 0; i--) {
    const f = actions[i]?.frame;
    if (typeof f === "number" && f >= 0 && f < frameCount) return f;
  }
  return 0;
}

/** One-line, secret-free description of a recorded step. */
export function describeAction(a: RecordingAction): string {
  const target = a.selector ?? a.url ?? a.page_url ?? "";
  const extra =
    a.text_chars !== undefined
      ? ` (${a.text_chars} chars)`
      : a.script_chars !== undefined
        ? ` (${a.script_chars} chars of JS)`
        : "";
  return `${a.tool.replace(/^browser_/, "")} ${target}${extra}`.trim();
}

export function formatAttention(
  template: string,
  attention: BrowserAttention,
): string {
  let host = attention.url;
  try {
    host = new URL(attention.url).host || attention.url;
  } catch {
    /* keep the raw string */
  }
  return template.replace("{kind}", attention.kind).replace("{url}", host);
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

export function useBrowserProfiles() {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["browser", "profiles"],
    queryFn: async () =>
      json<{ enabled: boolean; profiles: BrowserProfile[] }>(
        await fetch(`${base()}/api/browser/profiles`),
      ),
  });
  const mutate = useCallback(
    async (method: "DELETE" | "POST", path: string) => {
      await fetch(`${base()}/api/browser/${path}`, {
        method,
        headers: { ...getCsrfHeaders() },
      });
      await qc.invalidateQueries({ queryKey: ["browser", "profiles"] });
    },
    [qc],
  );
  return {
    ...query,
    remove: (name: string) =>
      mutate("DELETE", `profiles/${encodeURIComponent(name)}`),
    makeDefault: (name: string) =>
      mutate("POST", `profiles/${encodeURIComponent(name)}/default`),
    clearDefault: () => mutate("DELETE", "profiles-default"),
  };
}

export function useThreadBrowserState(
  threadId: string | null,
  enabled: boolean,
) {
  const qc = useQueryClient();
  const key = useMemo(() => ["browser", "state", threadId], [threadId]);
  const query = useQuery({
    queryKey: key,
    queryFn: async () =>
      json<ThreadBrowserState>(await fetch(threadPath(threadId!, "/state"))),
    enabled: Boolean(threadId) && enabled,
    // The captcha prompt has to appear while the agent is waiting on it.
    refetchInterval: 5000,
  });
  const resolve = useCallback(async () => {
    await fetch(threadPath(threadId!, "/attention/resolve"), {
      method: "POST",
      headers: { ...getCsrfHeaders() },
    });
    await qc.invalidateQueries({ queryKey: key });
  }, [qc, threadId, key]);
  const setRecording = useCallback(
    async (on: boolean) => {
      await fetch(threadPath(threadId!, "/recording"), {
        method: "PUT",
        headers: { "Content-Type": "application/json", ...getCsrfHeaders() },
        body: JSON.stringify({ enabled: on }),
      });
      await qc.invalidateQueries({ queryKey: key });
    },
    [qc, threadId, key],
  );
  return { ...query, resolve, setRecording };
}

export function useRecordings(threadId: string | null, enabled: boolean) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["browser", "recordings", threadId],
    queryFn: async () =>
      json<{ recordings: RecordingSummary[] }>(
        await fetch(threadPath(threadId!, "/recordings")),
      ),
    enabled: Boolean(threadId) && enabled,
    refetchInterval: enabled ? 15000 : false,
  });
  const remove = useCallback(
    async (sessionId: string) => {
      await fetch(
        threadPath(threadId!, `/recordings/${encodeURIComponent(sessionId)}`),
        { method: "DELETE", headers: { ...getCsrfHeaders() } },
      );
      await qc.invalidateQueries({
        queryKey: ["browser", "recordings", threadId],
      });
    },
    [qc, threadId],
  );
  return { ...query, remove };
}

export function useRecordingManifest(
  threadId: string | null,
  sessionId: string | null,
) {
  return useQuery({
    queryKey: ["browser", "recording", threadId, sessionId],
    queryFn: async () =>
      json<RecordingManifest>(
        await fetch(
          threadPath(
            threadId!,
            `/recordings/${encodeURIComponent(sessionId!)}`,
          ),
        ),
      ),
    enabled: Boolean(threadId && sessionId),
  });
}
