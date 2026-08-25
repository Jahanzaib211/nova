"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

/**
 * Poll and freshness intervals, named rather than scattered as literals.
 *
 * These were nine bare numbers spread through the file, written inconsistently
 * (`30_000` in one place, `30000` elsewhere in the codebase), so the cost of
 * the Agent's Computer panel — several polls per thread, running the whole time
 * it is open — was not visible anywhere. Grouping them makes that legible and
 * gives one place to tune it. The values are unchanged.
 */
const POLL = {
  /** Live output; the panel feels laggy above this. */
  FAST_MS: 2_000,
  /** Dev-server list — changes only when the agent starts or stops one. */
  DEV_SERVERS_MS: 3_000,
  /** Audit trail; append-only, so staleness is cheap. */
  AUDIT_MS: 4_000,
  /** Browser-check result, written once per verification run. */
  BROWSER_CHECK_MS: 5_000,
  /** File listing — the most expensive of these, and the least urgent. */
  FILES_MS: 10_000,
} as const;

const STALE = {
  /** Review payload; regenerating is explicit, so this can sit. */
  REVIEW_MS: 30_000,
  /** Terminal/VNC URLs are stable for the life of the sandbox. */
  SANDBOX_URLS_MS: 60_000,
} as const;

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
  /** Correlates the frames of one streamed command. Absent on the
      single-line observations every non-streaming tool still writes. */
  id?: string;
  /** "running" while output is still arriving; "done" on the closing frame. */
  state?: "running" | "done";
  /** Text to append to `output`. Wire-only: folded away at ingest. */
  delta?: string;
  /** Replacement for the whole of `output`, for a redrawn screen
      (a `\r` progress bar). Wire-only: folded away at ingest. */
  replace?: string;
  /** Stable client-side identity, assigned once when the event is first seen.
      React keys must not be derived from the array index: the MAX_EVENTS
      window shifts every element once it rolls, which remounts the whole list
      mid-stream. Not part of the wire format. */
  uid?: string;
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

/** Monotonic source of `uid`. Module-scoped so ids stay unique across threads
    and remounts within a session; the value itself is never meaningful. */
let uidCounter = 0;

/**
 * Fold one incoming frame into the event list.
 *
 * Frames sharing an `id` are one Terminal entry, and they are merged **here**,
 * at ingest, rather than pushed individually and reconciled at render. That
 * ordering is the whole point: pushed as separate events, a chatty command
 * (`npm install` emits hundreds of deltas) would walk its own opening frame out
 * of the MAX_EVENTS window, so the command line itself — the `$ npm install`
 * the user is reading — would vanish while its output was still arriving.
 *
 * `delta` appends, `replace` swaps the body, and the closing frame carries the
 * complete output so a client that opened the panel mid-command still ends up
 * with the whole thing rather than the tail it happened to catch.
 */
export function applySandboxFrame(
  list: SandboxEvent[],
  frame: SandboxEvent,
): SandboxEvent[] {
  // No id — the shape every other tool writes. Unchanged behaviour.
  if (!frame.id) {
    return list.concat({ ...frame, uid: `sbx-${uidCounter++}` });
  }

  const index = list.findIndex((e) => e.id === frame.id);
  if (index === -1) {
    return list.concat({
      ...frame,
      output: frame.replace ?? frame.delta ?? frame.output ?? "",
      delta: undefined,
      replace: undefined,
      uid: `sbx-${uidCounter++}`,
    });
  }

  const prev = list[index]!;
  const output =
    // `??` not `||`: an empty `replace` is a deliberate "clear the body".
    frame.replace ??
    (frame.delta !== undefined
      ? prev.output + frame.delta
      : // The closing frame carries the full output; an empty one (a kill,
        // say) must not blank an entry that already has text.
        frame.output || prev.output);

  const next = list.slice();
  next[index] = {
    ...prev,
    output,
    state: frame.state ?? prev.state,
    // Delta frames carry no summary; the opening frame's `$ command` stands.
    summary: frame.summary || prev.summary,
    path: frame.path ?? prev.path,
  };
  return next;
}

// ── useSandboxLogs ─────────────────────────────────────────
// SSE stream; parses each line as a JSON SandboxEvent.

/**
 * Reconnect backoff for the log stream.
 *
 * This replaces a hard stop after three failed opens. `/api/sandbox/logs` 404s
 * for any thread whose directory does not exist yet, so a thread whose sandbox
 * comes up late failed three times, closed the stream, and then showed an empty
 * Terminal *forever* — nothing re-armed it except navigating to another thread
 * and back. The reason for the cap was real, though: an EventSource left to its
 * own devices retries a 404 on a tight loop and logs a console error each time.
 *
 * Backing off geometrically to a 30 s ceiling answers both: a late sandbox is
 * picked up within half a minute, and a thread that never gets one costs two
 * requests a minute instead of a flood.
 */
const RECONNECT = {
  BASE_MS: 1_000,
  MAX_MS: 30_000,
} as const;

/** Must match `_SSE_DELTA_EVENT` in `app/gateway/routers/sandbox.py`. */
const SANDBOX_DELTA_EVENT = "sandbox_delta";

export type SandboxLogStatus = "connecting" | "open" | "reconnecting";

export type SandboxLogStream = {
  events: SandboxEvent[];
  /** Surfaced so the Terminal can say "reconnecting…" instead of looking idle. */
  status: SandboxLogStatus;
};

export function useSandboxLogs(
  threadId: string | null,
  enabled = true,
): SandboxLogStream {
  const [events, setEvents] = useState<SandboxEvent[]>([]);
  const [status, setStatus] = useState<SandboxLogStatus>("connecting");
  const esRef = useRef<EventSource | null>(null);
  const pendingRef = useRef<SandboxEvent[]>([]);
  const rafRef = useRef<number | null>(null);
  const attemptRef = useRef(0);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(() => {
    if (pendingRef.current.length === 0) return;
    setEvents((prev) => {
      // Fold first, window second. Reversing these is the bug this replaces:
      // deltas would consume window slots and evict their own start event.
      let next = prev;
      for (const frame of pendingRef.current) {
        next = applySandboxFrame(next, frame);
      }
      pendingRef.current = [];
      return next.length > MAX_EVENTS
        ? next.slice(next.length - MAX_EVENTS)
        : next;
    });
    rafRef.current = null;
  }, []);

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
    if (!threadId || !enabled || typeof EventSource === "undefined") return;

    let cancelled = false;

    const clearRetry = () => {
      if (retryRef.current !== null) {
        clearTimeout(retryRef.current);
        retryRef.current = null;
      }
    };

    const connect = () => {
      if (cancelled) return;
      esRef.current?.close();

      const url = `${getBackendBaseURL()}/api/sandbox/logs?thread_id=${encodeURIComponent(threadId)}`;
      const es = new EventSource(url, { withCredentials: true });
      esRef.current = es;

      es.onopen = () => {
        if (cancelled) return;
        attemptRef.current = 0;
        setStatus("open");
      };

      const ingest = (raw: string) => {
        if (!raw || raw === "[KEEPALIVE]") return;
        // A delivered line proves the stream is healthy, whatever onopen said.
        attemptRef.current = 0;
        setStatus("open");
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

      es.onmessage = (event) => ingest(event.data as string);

      // Incremental frames arrive under a NAMED event, which `onmessage` never
      // receives. That is the point: a build of this app that predates
      // streaming ignores them entirely and shows one row per command, instead
      // of rendering each delta as a blank row — which is exactly what happened
      // when they were sent as ordinary `message` events, because the backend
      // hot-reloads while this bundle is prebuilt and shipped separately.
      // `event` is already a MessageEvent here via EventSource's typing.
      es.addEventListener(SANDBOX_DELTA_EVENT, (event) => ingest(event.data));

      es.onerror = () => {
        if (cancelled) return;
        // Close before scheduling: the browser's own retry is what turns a 404
        // into a console-error flood, and we want our backoff, not its loop.
        es.close();
        esRef.current = null;
        setStatus("reconnecting");

        const delay = Math.min(
          RECONNECT.BASE_MS * 2 ** attemptRef.current,
          RECONNECT.MAX_MS,
        );
        attemptRef.current += 1;
        clearRetry();
        retryRef.current = setTimeout(connect, delay);
      };
    };

    attemptRef.current = 0;
    setStatus("connecting");
    connect();

    return () => {
      cancelled = true;
      clearRetry();
      esRef.current?.close();
      esRef.current = null;
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      pendingRef.current = [];
    };
  }, [threadId, enabled, flush]);

  return { events, status };
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

/**
 * Unwired: there is no Audit tab component. /api/sandbox/audit is live and
 * this polls it every 4s, but nothing renders the result.
 */
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
      if (!res.ok) return { events: [] };
      return res.json() as Promise<{ events: AuditEvent[] }>;
    },
    enabled: Boolean(threadId) && enabled,
    refetchInterval: POLL.AUDIT_MS,
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
  const query = useQuery<SandboxReview | null>({
    queryKey: ["sandbox", "review", threadId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/review?thread_id=${encodeURIComponent(threadId!)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      // review-tab.tsx reads review.files.length and review.checks; an
      // error body is truthy and would take the Review tab down with it.
      if (!res.ok) return null;
      return res.json() as Promise<SandboxReview>;
    },
    enabled: Boolean(threadId) && enabled,
    staleTime: STALE.REVIEW_MS,
    refetchOnWindowFocus: false,
  });
  return query;
}

export function sandboxReviewDownloadUrl(threadId: string): string {
  return `${getBackendBaseURL()}/api/sandbox/review?thread_id=${encodeURIComponent(threadId)}&download=true`;
}

// ── useSandboxTerminalUrl ──────────────────────────────────
// Same-origin URLs for the sandbox's interactive ttyd terminal + noVNC browser,
// for embedding live views in the Agent's Computer. Enable only when needed.
//
// The backend returns app-root-relative paths (/api/sandbox/appview/...). It
// used to return absolute http://localhost:{published_port} URLs, which only
// resolved when the browser happened to run on the Docker host — on any real
// deployment both panes were permanently blank, and the app shell's
// `frame-src 'self' blob:` CSP would have refused them regardless.

export type SandboxTerminalUrls = {
  terminal: string | null;
  vnc: string | null;
  port?: number;
  reason?: string;
};

const EMPTY_TERMINAL_URLS: SandboxTerminalUrls = { terminal: null, vnc: null };

export type SandboxTerminalUrlsResult = SandboxTerminalUrls & {
  /** Re-ask the gateway for the URLs. A sandbox that was recycled hands out a
      new published port, and the old iframe src points at a dead ttyd — with
      `staleTime` holding the stale answer, the pane showed a dead terminal
      indefinitely and offered no way to say "try again". */
  refetch: () => void;
  isFetching: boolean;
};

export function useSandboxTerminalUrl(
  threadId: string | null,
  enabled: boolean,
): SandboxTerminalUrlsResult {
  const { data, refetch, isFetching } = useQuery<SandboxTerminalUrls>({
    queryKey: ["sandbox", "terminal-url", threadId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/terminal-url?thread_id=${encodeURIComponent(threadId!)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      // Without this an error body becomes `{detail: ...}`, `raw.terminal` is
      // undefined, abs() returns undefined, and the object is still truthy --
      // so `data ?? {terminal: null, vnc: null}` below never fires and the
      // terminal and VNC panes spin on a loader forever with no error shown.
      // A reason rides along so panes can show *why* instead of just failing.
      if (!res.ok)
        return { ...EMPTY_TERMINAL_URLS, reason: `HTTP ${res.status}` };
      const raw = (await res.json()) as SandboxTerminalUrls;
      // Prefix root-relative paths so split-origin deployments (where
      // NEXT_PUBLIC_BACKEND_BASE_URL is set) still reach the gateway.
      const base = getBackendBaseURL();
      const abs = (u: string | null) =>
        u?.startsWith("/") ? `${base}${u}` : u;
      return { ...raw, terminal: abs(raw.terminal), vnc: abs(raw.vnc) };
    },
    enabled: Boolean(threadId) && enabled,
    staleTime: STALE.SANDBOX_URLS_MS,
    // Re-validate when the user comes back to the tab. The URL is stable for
    // the life of the sandbox, but not across a recycle — and returning to a
    // dead pane is exactly when a stale answer is most expensive.
    refetchOnWindowFocus: true,
  });
  return {
    ...(data ?? EMPTY_TERMINAL_URLS),
    refetch: () => void refetch(),
    isFetching,
  };
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
  const { data } = useQuery<BrowserCheckResult | null>({
    queryKey: ["sandbox", "browser-check-last", threadId],
    queryFn: async () => {
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/browser-check-last?thread_id=${encodeURIComponent(threadId!)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      // browser-tab.tsx reads autoTest.routes.length; `data ?? null` does
      // not rescue a {detail} body, only a missing one.
      if (!res.ok) return null;
      return res.json() as Promise<BrowserCheckResult>;
    },
    enabled: Boolean(threadId) && enabled,
    refetchInterval: POLL.BROWSER_CHECK_MS,
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
  status: string; // starting | ready | error | stopped | crashed
  host: string | null;
  port: number | null;
  url: string | null;
  /**
   * Absolute (gateway-origin) URL to the generic absproxy endpoint for the
   * same host:port. Falls back to this when the canonical preview proxy cannot
   * be reached (e.g. a dev server started outside ``start_dev_server`` whose
   * host the gateway can't route via the in-container preview port).
   */
  absproxyUrl: string | null;
  label?: string;
  compiles?: number; // increments on recompile → preview auto-reloads
};

const STOPPED_DEV_SERVER: DevServerStatus = {
  running: false,
  status: "stopped",
  host: null,
  port: null,
  url: null,
  absproxyUrl: null,
  compiles: 0,
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
          host: null,
          port: null,
          url: null,
          absproxyUrl: null,
          compiles: 0,
        };
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/dev-status?thread_id=${encodeURIComponent(threadId)}&label=${encodeURIComponent(label)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      if (!res.ok) return STOPPED_DEV_SERVER;
      // The gateway speaks snake_case (`absproxy_url`, routers/sandbox.py:584);
      // this type is camelCase. The bare cast meant absproxyUrl was ALWAYS
      // undefined, so the documented preview fallback at browser-tab.tsx:167
      // could never fire and a failed preview proxy showed a blank iframe
      // forever. TypeScript could not catch it -- nothing validated the shape.
      const raw = (await res.json()) as Record<string, unknown>;
      return {
        running: Boolean(raw.running),
        status: (raw.status as DevServerStatus["status"]) ?? "stopped",
        host: (raw.host as string | null) ?? null,
        port: (raw.port as number | null) ?? null,
        url: (raw.url as string | null) ?? null,
        absproxyUrl:
          (raw.absproxy_url as string | null) ??
          (raw.absproxyUrl as string | null) ??
          null,
        ...(typeof raw.label === "string" ? { label: raw.label } : {}),
        ...(typeof raw.compiles === "number" ? { compiles: raw.compiles } : {}),
      };
    },
    enabled: Boolean(threadId),
    refetchInterval: POLL.FAST_MS,
    refetchIntervalInBackground: false,
  });
  return (
    data ?? {
      running: false,
      status: "stopped",
      host: null,
      port: null,
      url: null,
      absproxyUrl: null,
    }
  );
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
      if (!res.ok) return { servers: [] };
      return res.json() as Promise<{ servers: DevServerEntry[] }>;
    },
    enabled: Boolean(threadId),
    refetchInterval: POLL.DEV_SERVERS_MS,
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
      if (!res.ok) return { files: [] };
      return res.json() as Promise<{ files: SandboxFile[] }>;
    },
    enabled: Boolean(threadId),
    refetchInterval: POLL.FILES_MS,
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
): { content: string; exists: boolean; lineCount: number; isLoading: boolean } {
  const { data, isPending } = useQuery<{ content: string; exists: boolean; size: number }>(
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
    // Distinguishes "still fetching" from a definitive answer. Without it the
    // Editor rendered an eternal spinner for missing AND empty files alike.
    isLoading: isPending && enabled && Boolean(threadId) && Boolean(path),
  };
}

// ── useSandboxFile ─────────────────────────────────────────
// Polls /api/sandbox/file every 2 s for a specific path.

// One warning per failing path — the 2 s poll would otherwise flood the console.
const warnedFilePaths = new Set<string>();

export function useSandboxFile(
  threadId: string | null,
  path: string | null,
  enabled = true,
): { content: string; exists: boolean; size: number; isLoading: boolean } {
  const { data, isPending } = useQuery<{
    content: string;
    exists: boolean;
    size: number;
  }>({
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
    // `enabled` lets callers stop the 2s poll when the result is not being
    // shown. `refetchIntervalInBackground: false` only covers a backgrounded
    // *window* — it does nothing for a tab that is merely `hidden` via CSS
    // while staying mounted, which is how the Agent's Computer works. Without
    // this the panel re-downloaded the whole deliverable 30x/minute forever,
    // including while the live dev-server preview made the result unused.
    enabled: Boolean(threadId) && Boolean(path) && enabled,
    refetchInterval: POLL.FAST_MS,
    refetchIntervalInBackground: false,
    // Keep the previous file's data while a NEW PATH in the SAME thread loads.
    // Changing `path` changes the query key, so without this `data` is briefly
    // undefined and the UI flashed "file is missing" for one round-trip on
    // every click. But a THREAD switch must NOT carry data over: the previous
    // thread's deliverable would render as this thread's preview until the
    // fetch resolved.
    placeholderData: (prev, prevQuery) =>
      prev && prevQuery?.queryKey[2] === threadId ? prev : undefined,
  });

  return {
    ...(data ?? { content: "", exists: false, size: 0 }),
    isLoading: isPending,
  };
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

export const EMPTY_TODO_RESULT: SandboxTodoResult = { content: "", todos: [] };

// The endpoint is typed, not validated. Trust the shape only after checking
// it, and hand back one shared empty value so callers' dependency arrays stay
// referentially stable across polls.
export function normalizeTodoResult(body: unknown): SandboxTodoResult {
  const raw = body as Partial<SandboxTodoResult> | null;
  if (!Array.isArray(raw?.todos)) return EMPTY_TODO_RESULT;
  return {
    content: typeof raw?.content === "string" ? raw.content : "",
    todos: raw.todos,
  };
}

export function useSandboxTodo(threadId: string | null): SandboxTodoResult {
  const { data } = useQuery<SandboxTodoResult>({
    queryKey: ["sandbox", "todo", threadId],
    queryFn: async () => {
      if (!threadId) return EMPTY_TODO_RESULT;
      const res = await fetch(
        `${getBackendBaseURL()}/api/sandbox/todo?thread_id=${encodeURIComponent(threadId)}`,
        { method: "GET", headers: { "Content-Type": "application/json" } },
      );
      // A 404 here is routine, not exceptional: the ownership guard rejects
      // any thread whose directory does not exist yet, which is every
      // brand-new chat. Without this check FastAPI's `{detail: "Not found"}`
      // body parsed as a successful result and left `todos` undefined for
      // the caller to crash on. Same reason as useSandboxLiveFile above.
      if (!res.ok) return EMPTY_TODO_RESULT;
      return normalizeTodoResult(await res.json());
    },
    enabled: Boolean(threadId),
    refetchInterval: POLL.FAST_MS,
    refetchIntervalInBackground: false,
  });

  return data ?? EMPTY_TODO_RESULT;
}
