"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

// ── Event types ────────────────────────────────────────────

export type SandboxEventType =
  | "bash"
  | "write_file"
  | "str_replace"
  | "read_file"
  | string;

export type SandboxEvent = {
  ts: string;
  type: SandboxEventType;
  path: string | null;
  summary: string;
  output: string;
};

export type SandboxFile = {
  path: string;
  virtual_path: string;
  name: string;
  size: number;
  mtime?: number;
  modified: string;
};

const MAX_EVENTS = 200;

// ── useSandboxLogs ─────────────────────────────────────────
// SSE stream; parses each line as a JSON SandboxEvent.

export function useSandboxLogs(threadId: string | null): SandboxEvent[] {
  const [events, setEvents] = useState<SandboxEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const pendingRef = useRef<SandboxEvent[]>([]);
  const rafRef = useRef<number | null>(null);
  const flush = () => {
    if (pendingRef.current.length === 0) return;
    setEvents((prev) => {
      const next = prev.concat(pendingRef.current);
      pendingRef.current = [];
      return next.length > MAX_EVENTS
        ? next.slice(next.length - MAX_EVENTS)
        : next;
    });
    rafRef.current = null;
  };

  useEffect(() => {
    // Reset on thread change
    setEvents([]);
    pendingRef.current = [];
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, [threadId]);

  useEffect(() => {
    if (!threadId || typeof EventSource === "undefined") return;

    esRef.current?.close();

    const url = `${getBackendBaseURL()}/api/sandbox/logs?thread_id=${encodeURIComponent(threadId)}`;
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    es.onmessage = (event) => {
      const raw = event.data as string;
      if (!raw || raw === "[KEEPALIVE]") return;
      try {
        const parsed = JSON.parse(raw) as SandboxEvent;
        if (parsed.type && parsed.ts !== undefined) {
          pendingRef.current.push(parsed);
          rafRef.current ??= requestAnimationFrame(flush);
        }
      } catch {
        // Non-JSON line (old format or noise) — skip silently
      }
    };

    es.onerror = () => {
      // SSE reconnects automatically; suppress noise
    };

    return () => {
      es.close();
      esRef.current = null;
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      pendingRef.current = [];
    };
  }, [threadId]);

  return events;
}

// ── useSandboxFiles ────────────────────────────────────────
// Polls /api/sandbox/files every 3 s.

// ── useSandboxAudit ────────────────────────────────────────
// Structured audit trail (every tool call) for the Audit tab.

export type AuditEvent = {
  ts?: string;
  type?: string;
  path?: string | null;
  summary?: string;
  output?: string;
};

export function useSandboxAudit(
  threadId: string | null,
  enabled = true,
): AuditEvent[] {
  const { data } = useQuery<{ events: AuditEvent[] }>({
    queryKey: ["sandbox", "audit", threadId],
    queryFn: async () => {
      if (!threadId) return { events: [] };
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/audit?thread_id=${encodeURIComponent(threadId)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      return res.json() as Promise<{ events: AuditEvent[] }>;
    },
    enabled: Boolean(threadId) && enabled,
    refetchInterval: 4000,
    refetchIntervalInBackground: false,
  });
  return data?.events ?? [];
}

export function sandboxAuditDownloadUrl(threadId: string): string {
  return `${getBackendBaseURL()}/api/sandbox/audit?thread_id=${encodeURIComponent(threadId)}&download=true`;
}

// ── useSandboxReview ───────────────────────────────────────
// Deterministic dual-audience code review (plain-English + developer detail).

export type ReviewFile = {
  path: string;
  added: number;
  removed: number;
  status: string;
};
export type ReviewRisk = { level: string; message: string; evidence?: string };
export type SandboxReview = {
  files: ReviewFile[];
  risks: ReviewRisk[];
  checks: Record<string, string>;
  added_total: number;
  removed_total: number;
  is_git: boolean;
  markdown: string;
};

export function useSandboxReview(threadId: string | null, enabled = true) {
  const query = useQuery<SandboxReview>({
    queryKey: ["sandbox", "review", threadId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/review?thread_id=${encodeURIComponent(threadId!)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      return res.json() as Promise<SandboxReview>;
    },
    enabled: Boolean(threadId) && enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  return query;
}

export function sandboxReviewDownloadUrl(threadId: string): string {
  return `${getBackendBaseURL()}/api/sandbox/review?thread_id=${encodeURIComponent(threadId)}&download=true`;
}

// ── useSandboxTerminalUrl ──────────────────────────────────
// Direct host URLs for the sandbox's interactive ttyd terminal + noVNC browser,
// for embedding live views in the Agent's Computer. Enable only when needed.

export type SandboxTerminalUrls = {
  terminal: string | null;
  vnc: string | null;
  port?: number;
  reason?: string;
};

export function useSandboxTerminalUrl(
  threadId: string | null,
  enabled: boolean,
): SandboxTerminalUrls {
  const { data } = useQuery<SandboxTerminalUrls>({
    queryKey: ["sandbox", "terminal-url", threadId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/terminal-url?thread_id=${encodeURIComponent(threadId!)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      return res.json() as Promise<SandboxTerminalUrls>;
    },
    enabled: Boolean(threadId) && enabled,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  return data ?? { terminal: null, vnc: null };
}

// ── useBrowserCheck ────────────────────────────────────────
// Agent browser self-test: loads the running app in the sandbox's native browser
// and reports console errors + render failures + a screenshot per route.

export type BrowserCheckRoute = {
  route: string;
  ok: boolean;
  status: string;
  console_errors: string[];
  notes: string;
  screenshot: string | null; // data URL
};
export type BrowserCheckResult = {
  ok: boolean;
  reason: string;
  port?: number | null;
  routes: BrowserCheckRoute[];
};

// Polls the latest self-test (auto-run on every preview, or a manual run) so the
// Browser tab shows results without anyone clicking. Enable only when a server is up.
export function useLastBrowserCheck(threadId: string | null, enabled: boolean) {
  const { data } = useQuery<BrowserCheckResult>({
    queryKey: ["sandbox", "browser-check-last", threadId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/browser-check-last?thread_id=${encodeURIComponent(threadId!)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      return res.json() as Promise<BrowserCheckResult>;
    },
    enabled: Boolean(threadId) && enabled,
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  });
  return data ?? null;
}

export function useBrowserCheck(threadId: string | null) {
  const [result, setResult] = useState<BrowserCheckResult | null>(null);
  const [running, setRunning] = useState(false);

  // Tracks the *current* threadId for the in-flight guard below — a plain
  // closure comparison doesn't work here since `run` is recreated (new
  // closure) whenever threadId changes, so a captured `threadId` would
  // always equal itself even for a stale in-flight call.
  const latestThreadIdRef = useRef(threadId);
  latestThreadIdRef.current = threadId;

  // The Browser tab isn't remounted per-thread (only the panel is), so
  // without this a manual self-test result from a previous thread would
  // keep rendering after switching threads until the user re-runs it.
  useEffect(() => {
    setResult(null);
    setRunning(false);
  }, [threadId]);

  const run = useCallback(
    async (label = "app", routes = "/") => {
      if (!threadId) return;
      const requestedFor = threadId;
      setRunning(true);
      try {
        const res = await fetch(
          `${getBackendBaseURL()}/api/sandbox/browser-check?thread_id=${encodeURIComponent(threadId)}&label=${encodeURIComponent(label)}&routes=${encodeURIComponent(routes)}`,
          { method: "POST" },
        );
        const data = (await res.json()) as BrowserCheckResult;
        // Guard against a slow response landing after the user has already
        // switched to a different thread.
        if (requestedFor === latestThreadIdRef.current) setResult(data);
      } catch {
        if (requestedFor === latestThreadIdRef.current) {
          setResult({ ok: false, reason: "request failed", routes: [] });
        }
      } finally {
        if (requestedFor === latestThreadIdRef.current) setRunning(false);
      }
    },
    [threadId],
  );
  return { result, running, run };
}

// ── useDevServerStatus ─────────────────────────────────────
// Polls /api/sandbox/dev-status every 2s — tells the Browser tab
// whether a live dev server is running and what URL to iframe.

export type DevServerStatus = {
  running: boolean;
  status: string; // starting | ready | error | stopped
  port: number | null;
  url: string | null;
  label?: string;
  compiles?: number; // increments on recompile → preview auto-reloads
};

export function useDevServerStatus(
  threadId: string | null,
  label = "app",
): DevServerStatus {
  const { data } = useQuery<DevServerStatus>({
    queryKey: ["sandbox", "dev-status", threadId, label],
    queryFn: async () => {
      if (!threadId)
        return {
          running: false,
          status: "stopped",
          port: null,
          url: null,
          compiles: 0,
        };
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/dev-status?thread_id=${encodeURIComponent(threadId)}&label=${encodeURIComponent(label)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      return res.json() as Promise<DevServerStatus>;
    },
    enabled: Boolean(threadId),
    refetchInterval: 2000,
    refetchIntervalInBackground: false,
  });
  return data ?? { running: false, status: "stopped", port: null, url: null };
}

// ── useDevServers ──────────────────────────────────────────
// Lists all live dev servers for a thread so the Browser tab can show a label
// dropdown when the agent runs more than one (multi-port apps).

export type DevServerEntry = {
  label: string;
  status: string;
  running: boolean;
  port: number | null;
  url: string | null;
};

// ── useStartPreview ────────────────────────────────────────
// Deterministically brings up the live preview (find project → install → start)
// via POST /dev-start — no LLM nudge. Used by the Browser tab button + auto-trigger.

export function useStartPreview(threadId: string | null) {
  return useCallback(
    async (label = "app") => {
      if (!threadId) return;
      try {
        await fetch(
          `${getBackendBaseURL()}/api/sandbox/dev-start?thread_id=${encodeURIComponent(threadId)}&label=${encodeURIComponent(label)}`,
          { method: "POST" },
        );
      } catch {
        // best-effort; dev-status polling will reflect the result
      }
    },
    [threadId],
  );
}

export function useDevServers(threadId: string | null): DevServerEntry[] {
  const { data } = useQuery<{ servers: DevServerEntry[] }>({
    queryKey: ["sandbox", "dev-servers", threadId],
    queryFn: async () => {
      if (!threadId) return { servers: [] };
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/dev-servers?thread_id=${encodeURIComponent(threadId)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      return res.json() as Promise<{ servers: DevServerEntry[] }>;
    },
    enabled: Boolean(threadId),
    refetchInterval: 3000,
    refetchIntervalInBackground: false,
  });
  return data?.servers ?? [];
}

export function useSandboxFiles(threadId: string | null): SandboxFile[] {
  const { data } = useQuery<{ files: SandboxFile[] }>({
    queryKey: ["sandbox", "files", threadId],
    queryFn: async () => {
      if (!threadId) return { files: [] };
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/files?thread_id=${encodeURIComponent(threadId)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      return res.json() as Promise<{ files: SandboxFile[] }>;
    },
    enabled: Boolean(threadId),
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  });

  return data?.files ?? [];
}

// ── useLiveFileContent ────────────────────────────────────
// Polls /api/sandbox/file every 1 s — used by Editor tab for live code view.

export function useLiveFileContent(
  threadId: string | null,
  path: string | null,
  enabled: boolean,
): { content: string; exists: boolean; lineCount: number } {
  const { data } = useQuery<{ content: string; exists: boolean; size: number }>(
    {
      queryKey: ["sandbox", "live-file", threadId, path],
      queryFn: async () => {
        if (!threadId || !path) return { content: "", exists: false, size: 0 };
        const res = await fetch(
          `${getBackendBaseURL()}/api/sandbox/file?thread_id=${encodeURIComponent(threadId)}&path=${encodeURIComponent(path)}`,
          { method: "GET", headers: { "Content-Type": "application/json" } },
        );
        if (!res.ok) {
          // Surface transport failures as "missing" instead of letting
          // res.json() throw and leave the query stuck on stale data.
          const warnKey = `${threadId}:${path}:${res.status}`;
          if (!warnedFilePaths.has(warnKey)) {
            warnedFilePaths.add(warnKey);
            console.warn(
              `[nova] sandbox file read failed (${res.status}) for ${path}`,
            );
          }
          return { content: "", exists: false, size: 0 };
        }
        return res.json() as Promise<{
          content: string;
          exists: boolean;
          size: number;
        }>;
      },
      enabled: enabled && Boolean(threadId) && Boolean(path),
      refetchInterval: enabled ? 1000 : false,
      refetchIntervalInBackground: false,
    },
  );

  const content = data?.content ?? "";
  return {
    content,
    exists: data?.exists ?? false,
    lineCount: content.split("\n").length,
  };
}

// ── useSandboxFile ─────────────────────────────────────────
// Polls /api/sandbox/file every 2 s for a specific path.

// One warning per failing path — the 2 s poll would otherwise flood the console.
const warnedFilePaths = new Set<string>();

export function useSandboxFile(
  threadId: string | null,
  path: string | null,
): { content: string; exists: boolean; size: number } {
  const { data } = useQuery<{ content: string; exists: boolean; size: number }>(
    {
      queryKey: ["sandbox", "file", threadId, path],
      queryFn: async () => {
        if (!threadId || !path) return { content: "", exists: false, size: 0 };
        const res = await fetch(
          `${getBackendBaseURL()}/api/sandbox/file?thread_id=${encodeURIComponent(threadId)}&path=${encodeURIComponent(path)}`,
          { method: "GET", headers: { "Content-Type": "application/json" } },
        );
        if (!res.ok) {
          // Surface transport failures as "missing" instead of letting
          // res.json() throw and leave the query stuck on stale data.
          const warnKey = `${threadId}:${path}:${res.status}`;
          if (!warnedFilePaths.has(warnKey)) {
            warnedFilePaths.add(warnKey);
            console.warn(
              `[nova] sandbox file read failed (${res.status}) for ${path}`,
            );
          }
          return { content: "", exists: false, size: 0 };
        }
        return res.json() as Promise<{
          content: string;
          exists: boolean;
          size: number;
        }>;
      },
      enabled: Boolean(threadId) && Boolean(path),
      refetchInterval: 2000,
      refetchIntervalInBackground: false,
    },
  );

  return data ?? { content: "", exists: false, size: 0 };
}

// ── useSandboxTodo (kept for compatibility) ────────────────

export type SandboxTodo = {
  description: string;
  status: "pending" | "in_progress" | "completed" | "done";
};

export type SandboxTodoResult = {
  content: string;
  todos: SandboxTodo[];
};

export function useSandboxTodo(threadId: string | null): SandboxTodoResult {
  const { data } = useQuery<SandboxTodoResult>({
    queryKey: ["sandbox", "todo", threadId],
    queryFn: async () => {
      if (!threadId) return { content: "", todos: [] };
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/todo?thread_id=${encodeURIComponent(threadId)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      return res.json() as Promise<SandboxTodoResult>;
    },
    enabled: Boolean(threadId),
    refetchInterval: 2000,
    refetchIntervalInBackground: false,
  });

  return data ?? { content: "", todos: [] };
}
