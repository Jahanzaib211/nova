import type { AIMessage, Message, Run } from "@langchain/langgraph-sdk";
import type { ThreadsClient } from "@langchain/langgraph-sdk/client";
import { useStream } from "@langchain/langgraph-sdk/react";
import {
  type QueryClient,
  type InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";

import { getAPIClient } from "../api";
import { fetch } from "../api/fetcher";
import { getStreamLiveness } from "../api/stream-liveness";
import { getBackendBaseURL } from "../config";
import { useI18n } from "../i18n/hooks";
import { isHiddenFromUIMessage } from "../messages/utils";
import type { FileInMessage } from "../messages/utils";
import type { LocalSettings } from "../settings";
import { useUpdateSubtask } from "../tasks/context";
import type { UploadedFileInfo } from "../uploads";
import { promptInputFilePartToFile, uploadFiles } from "../uploads";

import { fetchThreadTokenUsage } from "./api";
import {
  classifyVerifyOutcome,
  evaluateWatchdog,
  nextStopState,
  recordCleanup,
  recordCompletion,
  recordHookEvent,
  recordManagerState,
  recordRecovery,
  recordReconnect,
  recordRejoinAttempt,
  recordStallWatchdog,
  recordStateMerge,
  shouldArmWatchdog,
} from "./stream-trace";
import { threadTokenUsageQueryKey } from "./token-usage";
import type {
  AgentThread,
  AgentThreadState,
  RunMessage,
  ThreadTokenUsageResponse,
} from "./types";

const TIMEOUT_SENTINEL: unique symbol = Symbol.for("nova.threads.stop.timeout");

export type ToolEndEvent = {
  name: string;
  data: unknown;
};

export type TaskProgressEvent = {
  step: number;
  total: number;
  status: string;
};

export type VerifyResultRoute = {
  route: string;
  ok: boolean;
  status: number | null;
  notes: string;
};

export type VerifyResultEvent = {
  thread_id: string;
  ok: boolean;
  verdict: "passed" | "issues";
  routes: VerifyResultRoute[];
  console_errors_count: number;
  screenshot: string | null;
};

export type LlmErrorEvent = {
  error_type: string;
  reason: "quota" | "auth" | "busy" | "transient" | "circuit_open" | string;
  detail: string;
  http_status: number | null;
  code: string | null;
};

export type AgentActivityEvent = {
  id: string;
  ts: string;
  type: string;
  path: string | null;
  summary: string;
  output: string;
  status: "running" | "done" | "error";
};

function _buildActivitySummary(
  name: string,
  { path, cmd }: { path?: string | null; cmd?: string | null },
): string {
  const filename = path?.split("/").at(-1);
  if (name === "write_file")
    return filename ? `Writing ${filename}` : "Writing file";
  if (name === "str_replace")
    return filename ? `Editing ${filename}` : "Editing file";
  if (name === "read_file")
    return filename ? `Reading ${filename}` : "Reading file";
  if (name === "bash" || name === "execute_command")
    return cmd ? `$ ${cmd.slice(0, 60)}` : "Running command";
  if (name === "search_files") return "Searching files";
  if (name === "grep_files") return "Searching content";
  if (name === "task") return "Delegating to subagent";
  return name;
}

export type ThreadStreamOptions = {
  threadId?: string | null | undefined;
  displayThreadId?: string | null | undefined;
  context: LocalSettings["context"];
  isMock?: boolean;
  onSend?: (threadId: string) => void;
  onStart?: (threadId: string, runId: string) => void;
  onFinish?: (state: AgentThreadState) => void;
  onToolEnd?: (event: ToolEndEvent) => void;
  onToolActivity?: (event: AgentActivityEvent) => void;
  onToolActivityDone?: (info: {
    id: string;
    name: string;
    output: string;
    path: string | null;
    cmd: string | null;
  }) => void;
  onTaskProgress?: (progress: TaskProgressEvent) => void;
  onVerifyResult?: (event: VerifyResultEvent) => void;
  onLlmError?: (event: LlmErrorEvent) => void;
};

type SendMessageOptions = {
  additionalKwargs?: Record<string, unknown>;
};

const EMPTY_THREAD_VALUES: AgentThreadState = {
  title: "",
  messages: [],
  artifacts: [],
  todos: [],
};

function isNonEmptyString(value: string | undefined): value is string {
  return typeof value === "string" && value.length > 0;
}

const SUMMARIZATION_MIDDLEWARE_UPDATE_KEYS = new Set([
  "SummarizationMiddleware.before_model",
  "DeerFlowSummarizationMiddleware.before_model",
]);

function messageIdentity(message: Message): string | undefined {
  if (
    "tool_call_id" in message &&
    typeof message.tool_call_id === "string" &&
    message.tool_call_id.length > 0
  ) {
    return `tool:${message.tool_call_id}`;
  }
  if (typeof message.id === "string" && message.id.length > 0) {
    return `message:${message.id}`;
  }
  return undefined;
}

function dedupeMessagesByIdentity(messages: Message[]): Message[] {
  const lastIndexByIdentity = new Map<string, number>();
  const lastVisibleIndexByIdentity = new Map<string, number>();

  // This is a UI-display dedupe rule, not a general LangChain message-stream
  // contract. Hidden messages that share an identity with a visible message are
  // treated as control messages for this merged view; hidden messages carrying
  // independent tracing/task semantics should use a distinct id or a custom
  // stream/state channel instead of relying on message dedupe preservation.
  messages.forEach((message, index) => {
    const identity = messageIdentity(message);
    if (identity) {
      lastIndexByIdentity.set(identity, index);
      if (!isHiddenFromUIMessage(message)) {
        lastVisibleIndexByIdentity.set(identity, index);
      }
    }
  });

  return messages.filter((message, index) => {
    const identity = messageIdentity(message);
    if (!identity) {
      return true;
    }
    const visibleIndex = lastVisibleIndexByIdentity.get(identity);
    if (visibleIndex !== undefined) {
      return visibleIndex === index;
    }
    return lastIndexByIdentity.get(identity) === index;
  });
}

export function findLatestUnloadedRunIndex(
  runs: Run[],
  loadedRunIds: ReadonlySet<string>,
): number {
  for (let i = 0; i < runs.length; i++) {
    const run = runs[i];
    if (run && !loadedRunIds.has(run.run_id)) {
      return i;
    }
  }
  return -1;
}

export const MAX_CONSECUTIVE_EMPTY_RUN_LOADS = 5;

export function shouldAutoContinueOnEmptyRun(
  fetchedMessageCount: number,
  consecutiveEmptyLoads: number,
  maxConsecutiveEmptyLoads: number = MAX_CONSECUTIVE_EMPTY_RUN_LOADS,
): boolean {
  return (
    fetchedMessageCount === 0 &&
    consecutiveEmptyLoads < maxConsecutiveEmptyLoads
  );
}

type RunMessagesPageResponse = {
  data: RunMessage[];
  has_more?: boolean;
  hasMore?: boolean;
};

export function runMessagesPageHasMore(result: RunMessagesPageResponse) {
  return result.has_more ?? result.hasMore ?? false;
}

export function getOldestRunMessageSeq(messages: RunMessage[]) {
  let oldestSeq: number | null = null;
  for (const message of messages) {
    if (typeof message.seq !== "number") {
      continue;
    }
    oldestSeq =
      oldestSeq === null ? message.seq : Math.min(oldestSeq, message.seq);
  }
  return oldestSeq;
}

export function getNextRunMessagesBeforeSeq(
  result: RunMessagesPageResponse,
): number | null | undefined {
  if (!runMessagesPageHasMore(result)) {
    return null;
  }
  return getOldestRunMessageSeq(result.data) ?? undefined;
}

export function buildRunMessagesUrl(
  baseUrl: string,
  threadId: string,
  runId: string,
  beforeSeq?: number,
) {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");
  const path = `/api/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/messages`;
  const url = new URL(
    `${normalizedBaseUrl}${path}`,
    typeof window !== "undefined" ? window.location.origin : "http://localhost",
  );
  if (beforeSeq !== undefined) {
    url.searchParams.set("before_seq", String(beforeSeq));
  }
  return normalizedBaseUrl ? url.toString() : `${url.pathname}${url.search}`;
}

export function mergeMessages(
  historyMessages: Message[],
  threadMessages: Message[],
  optimisticMessages: Message[],
): Message[] {
  // Only visible live messages should trim overlapping history. Hidden messages
  // are UI control messages in this path, not observability records; any hidden
  // message that must survive as task/tracing data should use custom events or a
  // separate state channel instead of participating in this overlap heuristic.
  const threadMessageIds = new Set(
    threadMessages
      .filter((message) => !isHiddenFromUIMessage(message))
      .map(messageIdentity)
      .filter(isNonEmptyString),
  );

  // The overlap is a contiguous suffix of historyMessages (newest history == oldest thread).
  // Scan from the end: shrink cutoff while messages are already in thread, stop as soon as
  // we hit one that isn't — everything before that point is non-overlapping.
  let cutoff = historyMessages.length;
  for (let i = historyMessages.length - 1; i >= 0; i--) {
    const msg = historyMessages[i];
    if (!msg) {
      continue;
    }
    const identity = messageIdentity(msg);
    if (identity && threadMessageIds.has(identity)) {
      cutoff = i;
    } else {
      break;
    }
  }

  return dedupeMessagesByIdentity([
    ...historyMessages.slice(0, cutoff),
    ...threadMessages,
    ...optimisticMessages,
  ]);
}

function getMessagesAfterBaseline(
  messages: Message[],
  baselineMessageIds: ReadonlySet<string>,
): Message[] {
  return messages.filter((message) => {
    const id = messageIdentity(message);
    return !id || !baselineMessageIds.has(id);
  });
}

export function getVisibleOptimisticMessages(
  optimisticMessages: Message[],
  previousHumanMessageCount: number,
  currentHumanMessageCount: number,
): Message[] {
  if (
    optimisticMessages.some((message) => message.type === "human") &&
    currentHumanMessageCount > previousHumanMessageCount
  ) {
    return [];
  }
  return optimisticMessages;
}

export function getSummarizationMiddlewareMessages(
  data: unknown,
): Message[] | undefined {
  if (typeof data !== "object" || data === null) {
    return undefined;
  }

  for (const [key, update] of Object.entries(data)) {
    if (!SUMMARIZATION_MIDDLEWARE_UPDATE_KEYS.has(key)) {
      continue;
    }
    if (typeof update !== "object" || update === null) {
      continue;
    }

    const messages = Reflect.get(update, "messages");
    if (Array.isArray(messages)) {
      return [...messages] as Message[];
    }
  }

  return undefined;
}

export function upsertThreadInSearchCache(
  queryClient: QueryClient,
  thread: AgentThread,
) {
  queryClient.setQueriesData(
    {
      queryKey: ["threads", "search"],
      exact: false,
    },
    (oldData: Array<AgentThread> | undefined) => {
      if (!oldData) {
        return [thread];
      }

      const existingIndex = oldData.findIndex(
        (t) => t.thread_id === thread.thread_id,
      );
      if (existingIndex === -1) {
        return [thread, ...oldData];
      }

      return oldData.map((t, index) => {
        if (index !== existingIndex) {
          return t;
        }
        return {
          ...thread,
          ...t,
          metadata: {
            ...(thread.metadata ?? {}),
            ...(t.metadata ?? {}),
          },
          values: {
            ...thread.values,
            ...t.values,
          },
        };
      });
    },
  );
}

export function upsertThreadInInfiniteCache(
  queryClient: QueryClient,
  thread: AgentThread,
) {
  queryClient.setQueriesData(
    {
      queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      exact: false,
    },
    (oldData: InfiniteData<AgentThread[]> | undefined) => {
      if (!oldData) {
        return oldData;
      }

      const merged = oldData.pages.map((page) =>
        page.map((t) =>
          t.thread_id === thread.thread_id
            ? {
                ...thread,
                ...t,
                metadata: {
                  ...(thread.metadata ?? {}),
                  ...(t.metadata ?? {}),
                },
                values: {
                  ...thread.values,
                  ...t.values,
                },
              }
            : t,
        ),
      );

      const exists = merged.some((page) =>
        page.some((t) => t.thread_id === thread.thread_id),
      );
      if (exists) {
        return { ...oldData, pages: merged };
      }

      const firstPage = merged[0] ?? [];
      const restPages = merged.slice(1);
      return {
        ...oldData,
        pages: [[thread, ...firstPage], ...restPages],
      };
    },
  );
}

function isReconnectNoise(error: unknown): boolean {
  if (typeof error !== "object" || error === null) {
    return false;
  }
  // The SDK wraps the browser fetch failure into a ConnectionError (see
  // langgraph-sdk/src/utils/async_caller.ts onFailedAttempt). The same
  // shape leaks through to the UI as either a TypeError: Failed to fetch
  // (Chromium) or a NetworkError when attempting to fetch resource
  // (Firefox). Both indicate the server stream was already torn down
  // before we tried to reconnect — not a real error.
  const name = Reflect.get(error, "name");
  if (name === "ConnectionError") {
    return true;
  }
  const message = Reflect.get(error, "message");
  if (typeof message === "string") {
    return (
      message.includes("Failed to fetch") ||
      message.includes("NetworkError") ||
      message.includes("ECONNREFUSED") ||
      message.includes("Unable to connect to LangGraph server")
    );
  }
  const nestedMessage =
    Reflect.get(error, "error") &&
    Reflect.get(Reflect.get(error, "error"), "message");
  if (typeof nestedMessage === "string") {
    return (
      nestedMessage.includes("Failed to fetch") ||
      nestedMessage.includes("NetworkError") ||
      nestedMessage.includes("ECONNREFUSED")
    );
  }
  return false;
}

function getStreamErrorMessage(error: unknown): string {
  if (typeof error === "string" && error.trim()) {
    return error;
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  if (typeof error === "object" && error !== null) {
    const message = Reflect.get(error, "message");
    if (typeof message === "string" && message.trim()) {
      return message;
    }
    const nestedError = Reflect.get(error, "error");
    if (nestedError instanceof Error && nestedError.message.trim()) {
      return nestedError.message;
    }
    if (typeof nestedError === "string" && nestedError.trim()) {
      return nestedError;
    }
  }
  return "Request failed.";
}

function getHttpStatus(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null) {
    return undefined;
  }

  const status = Reflect.get(error, "status");
  if (typeof status === "number") {
    return status;
  }

  const response = Reflect.get(error, "response");
  if (typeof response === "object" && response !== null) {
    const responseStatus = Reflect.get(response, "status");
    if (typeof responseStatus === "number") {
      return responseStatus;
    }
  }

  return undefined;
}

function isThreadMissingError(error: unknown): boolean {
  const status = getHttpStatus(error);
  // Treat 403 like 404 here to avoid disclosing whether an inaccessible thread
  // exists; callers redirect stale/inaccessible URLs back to a blank chat.
  return status === 403 || status === 404;
}

export function useThreadStream({
  threadId,
  displayThreadId,
  context,
  isMock,
  onSend,
  onStart,
  onFinish,
  onToolEnd,
  onTaskProgress,
  onVerifyResult,
  onLlmError,
  onToolActivity,
  onToolActivityDone,
}: ThreadStreamOptions) {
  const { t } = useI18n();
  const currentViewThreadId = displayThreadId ?? threadId ?? null;
  const currentViewThreadIdRef = useRef(currentViewThreadId);
  currentViewThreadIdRef.current = currentViewThreadId;
  // Optimistic messages shown before the server stream responds.
  const [optimisticMessages, setOptimisticMessages] = useState<Message[]>([]);
  const [optimisticThreadId, setOptimisticThreadId] = useState<string | null>(
    null,
  );
  const [liveMessagesThreadId, setLiveMessagesThreadId] = useState<
    string | null
  >(null);
  const [isUploading, setIsUploading] = useState(false);
  // Track the thread ID that is currently streaming to handle thread changes during streaming
  const [onStreamThreadId, setOnStreamThreadId] = useState(() => threadId);
  // Ref to track current thread ID across async callbacks without causing re-renders,
  // and to allow access to the current thread id in onUpdateEvent
  const threadIdRef = useRef<string | null>(threadId ?? null);
  const startedRef = useRef(false);
  const pendingUsageBaselineMessageIdsRef = useRef<Set<string>>(new Set());
  // Observability: monotonic timestamp of the last SDK event we received.
  // Updated inside the onLangChainEvent callback. A null value means we
  // have not received any event yet since this hook mounted.
  const lastEventAtRef = useRef<number | null>(null);
  const lastEventIdRef = useRef<string | null>(null);
  // Captured from the SDK onCreated event so the watchdog has a stable run
  // identity for tracing even when useActiveRun hasn't polled any data yet
  // (the primary-path freeze window: stream wedges in the first 60s after
  // a send, before the active-run poll has data).
  const streamRunIdRef = useRef<string | null>(null);
  const listeners = useRef({
    onSend,
    onStart,
    onFinish,
    onToolEnd,
    onTaskProgress,
    onVerifyResult,
    onLlmError,
    onToolActivity,
    onToolActivityDone,
  });

  const {
    messages: history,
    hasMore: hasMoreHistory,
    loadMore: loadMoreHistory,
    loading: isHistoryLoading,
    appendMessages,
  } = useThreadHistory(onStreamThreadId ?? "", { enabled: !isMock });

  // Keep listeners ref updated with latest callbacks
  useEffect(() => {
    listeners.current = {
      onSend,
      onStart,
      onFinish,
      onToolEnd,
      onTaskProgress,
      onVerifyResult,
      onLlmError,
      onToolActivity,
      onToolActivityDone,
    };
  }, [
    onSend,
    onStart,
    onFinish,
    onToolEnd,
    onTaskProgress,
    onVerifyResult,
    onLlmError,
    onToolActivity,
    onToolActivityDone,
  ]);

  useEffect(() => {
    const normalizedThreadId = threadId ?? null;
    if (!normalizedThreadId) {
      // Reset when the UI moves back to a brand new unsaved thread.
      startedRef.current = false;
      setOnStreamThreadId(normalizedThreadId);
    } else {
      setOnStreamThreadId(normalizedThreadId);
    }
    threadIdRef.current = normalizedThreadId;
  }, [threadId]);

  const handleStreamStart = useCallback((_threadId: string, _runId: string) => {
    threadIdRef.current = _threadId;
    streamRunIdRef.current = _runId;
    setOptimisticThreadId((currentOptimisticThreadId) => {
      const currentView = currentViewThreadIdRef.current;
      if (
        currentOptimisticThreadId &&
        (currentOptimisticThreadId === currentView ||
          currentOptimisticThreadId === _threadId)
      ) {
        return _threadId;
      }
      return currentOptimisticThreadId;
    });
    setLiveMessagesThreadId((currentLiveMessagesThreadId) => {
      const currentView = currentViewThreadIdRef.current;
      if (
        currentLiveMessagesThreadId &&
        (currentLiveMessagesThreadId === currentView ||
          currentLiveMessagesThreadId === _threadId)
      ) {
        return _threadId;
      }
      return currentLiveMessagesThreadId;
    });
    if (!startedRef.current) {
      listeners.current.onStart?.(_threadId, _runId);
      startedRef.current = true;
    }
    setOnStreamThreadId(_threadId);
  }, []);

  const queryClient = useQueryClient();
  const updateSubtask = useUpdateSubtask();

  // Explicit rejoin lifecycle state (see the joinStream effect below the
  // useStream call). Declared here because onError needs it to classify
  // join failures before the effect exists.
  const rejoinStateRef = useRef({
    runId: null as string | null,
    attempts: 0,
    inFlight: false,
    exhausted: false,
  });

  const thread = useStream<AgentThreadState>({
    client: getAPIClient(isMock),
    assistantId: "lead_agent",
    threadId: onStreamThreadId,
    // Batch 2C.3 / v7.2 audit Fix 17: the SDK's reconnectOnMount caused
    // ConnectionError storms because it rejoins blindly from sessionStorage
    // even when the run is long gone. It stays off; the explicit rejoin
    // effect below owns reconnection instead — it verifies via the runs API
    // that a pending/running run actually exists before calling joinStream,
    // and gives up cleanly on 404/409.
    reconnectOnMount: false,
    fetchStateHistory: { limit: 1 },
    onCreated(meta) {
      handleStreamStart(meta.thread_id, meta.run_id);
      const now = new Date().toISOString();
      upsertThreadInSearchCache(queryClient, {
        thread_id: meta.thread_id,
        created_at: now,
        updated_at: now,
        metadata: context.agent_name ? { agent_name: context.agent_name } : {},
        status: "busy",
        values: {
          title: t.pages.newChat,
          messages: [],
          artifacts: [],
        },
        interrupts: {},
      });
      upsertThreadInInfiniteCache(queryClient, {
        thread_id: meta.thread_id,
        created_at: now,
        updated_at: now,
        metadata: context.agent_name ? { agent_name: context.agent_name } : {},
        status: "busy",
        values: {
          title: t.pages.newChat,
          messages: [],
          artifacts: [],
        },
        interrupts: {},
      });
      if (context.agent_name && !isMock) {
        void getAPIClient()
          .threads.update(meta.thread_id, {
            metadata: { agent_name: context.agent_name },
          })
          .catch(() => ({}));
      }
    },
    onLangChainEvent(event) {
      // Observability: every SDK event passes through this hook. Capture
      // the timestamp + event id so downstream liveness signals (stall
      // watchdog) can read them. NO production behaviour depends on this.
      const now =
        typeof performance !== "undefined" ? performance.now() : Date.now();
      lastEventAtRef.current = now;
      const eventId = (event as unknown as Record<string, unknown>).id as
        | string
        | undefined;
      if (typeof eventId === "string" && eventId.length > 0) {
        lastEventIdRef.current = eventId;
      }
      recordHookEvent(threadIdRef.current, null, event.event, eventId ?? null);

      if (event.event === "on_tool_start") {
        const raw = event.data as Record<string, unknown> | null | undefined;
        const input = (raw?.input ?? raw ?? {}) as Record<string, unknown>;
        const path = typeof input.path === "string" ? input.path : null;
        const cmd = typeof input.command === "string" ? input.command : null;
        const ts = new Date().toLocaleTimeString("en-US", {
          hour12: false,
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
        const id =
          ((event as unknown as Record<string, unknown>).run_id as
            | string
            | undefined) ?? `${event.name}-${Date.now()}`;
        listeners.current.onToolActivity?.({
          id,
          ts,
          type: event.name,
          path,
          summary: _buildActivitySummary(event.name, { path, cmd }),
          output: "",
          status: "running",
        });
      }

      if (event.event === "on_tool_end") {
        listeners.current.onToolEnd?.({
          name: event.name,
          data: event.data,
        });
        // Extract output, and try to get input.path/command from on_tool_end data
        // (available in some LangGraph SDK versions alongside the output)
        const raw = event.data as
          | Record<string, unknown>
          | string
          | null
          | undefined;
        const output =
          typeof raw === "string"
            ? raw
            : (((raw as Record<string, unknown> | null)?.output as
                | string
                | undefined) ?? "");
        const inputFromEnd =
          typeof raw !== "string"
            ? ((raw as Record<string, unknown> | null)?.input as
                | Record<string, unknown>
                | undefined)
            : undefined;
        const pathFromEnd =
          typeof inputFromEnd?.path === "string" ? inputFromEnd.path : null;
        const cmdFromEnd =
          typeof inputFromEnd?.command === "string"
            ? inputFromEnd.command
            : null;
        const id =
          ((event as unknown as Record<string, unknown>).run_id as
            | string
            | undefined) ?? "";
        // Pass full info so the callback can CREATE an event if on_tool_start never fired
        listeners.current.onToolActivityDone?.({
          id,
          name: event.name,
          output,
          path: pathFromEnd,
          cmd: cmdFromEnd,
        });
      }
    },
    onUpdateEvent(data) {
      const _messages = getSummarizationMiddlewareMessages(data);
      if (_messages && _messages.length >= 2) {
        for (const m of _messages) {
          if (m.name === "summary" && m.type === "human") {
            summarizedRef.current?.add(m.id ?? "");
          }
        }
        const firstRetainedVisibleIdentity = _messages
          .filter((message) => message.type !== "remove")
          .filter((message) => !isHiddenFromUIMessage(message))
          .map(messageIdentity)
          .find(isNonEmptyString);
        const _currentMessages = [...messagesRef.current];
        const _movedMessages: Message[] = [];
        for (const m of _currentMessages) {
          if (
            firstRetainedVisibleIdentity &&
            messageIdentity(m) === firstRetainedVisibleIdentity
          ) {
            break;
          }
          if (!summarizedRef.current?.has(m.id ?? "")) {
            _movedMessages.push(m);
          }
        }
        appendMessages(_movedMessages);
        messagesRef.current = [];
      }

      const updates: Array<Partial<AgentThreadState> | null> = Object.values(
        data || {},
      );
      for (const update of updates) {
        if (update && "title" in update && update.title) {
          void queryClient.setQueriesData(
            {
              queryKey: ["threads", "search"],
              exact: false,
            },
            (oldData: Array<AgentThread> | undefined) => {
              return oldData?.map((t) => {
                if (t.thread_id === threadIdRef.current) {
                  return {
                    ...t,
                    values: {
                      ...t.values,
                      title: update.title,
                    },
                  };
                }
                return t;
              });
            },
          );
          const nextTitle: string = update.title;
          void queryClient.setQueriesData(
            {
              queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
              exact: false,
            },
            (oldData: InfiniteData<AgentThread[]> | undefined) =>
              mapInfiniteThreadsCache(
                oldData,
                (t): AgentThread =>
                  t.thread_id === threadIdRef.current
                    ? {
                        ...t,
                        values: {
                          ...t.values,
                          title: nextTitle,
                        },
                      }
                    : t,
              ),
          );
        }
      }
    },
    onCustomEvent(event: unknown) {
      // The backend emits six task lifecycle events (task_tool.py:335-415);
      // this listened for exactly one of them. The other five had no handler
      // anywhere, so subtask status never came from the stream at all and fell
      // through to a liveness guess that reports "failed" whenever the runs
      // cache looks empty -- painting finished subagents red.
      //
      // All six carry "result" authority: the writer(...) call and the
      // ToolMessage string are built from the same result object in the same
      // branch, so they cannot disagree, and a result-sourced status outranks
      // the derived guess in nextSubtaskStatus.
      //
      // Guarded on `task_id` so sibling task-prefixed events with a different
      // shape (task_progress, task_activity) fall through to their own handlers.
      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        typeof event.type === "string" &&
        event.type.startsWith("task_") &&
        "task_id" in event
      ) {
        const e = event as {
          type: string;
          task_id: string;
          message?: AIMessage;
          description?: string;
          result?: string;
          error?: string;
        };
        switch (e.type) {
          case "task_started":
            updateSubtask(
              {
                id: e.task_id,
                status: "in_progress",
                ...(e.description !== undefined
                  ? { description: e.description }
                  : {}),
              },
              "result",
            );
            return;
          case "task_running":
            updateSubtask(
              {
                id: e.task_id,
                status: "in_progress",
                ...(e.message !== undefined
                  ? { latestMessage: e.message }
                  : {}),
              },
              "result",
            );
            return;
          case "task_completed":
            updateSubtask(
              {
                id: e.task_id,
                status: "completed",
                ...(e.result !== undefined ? { result: e.result } : {}),
              },
              "result",
            );
            return;
          // Cancelled and timed-out are failures as far as the card is
          // concerned; the error string is what distinguishes them.
          case "task_failed":
          case "task_cancelled":
          case "task_timed_out":
            updateSubtask(
              {
                id: e.task_id,
                status: "failed",
                ...(e.error ? { error: e.error } : {}),
              },
              "result",
            );
            return;
          default:
            break;
        }
      }

      // A second, independent "this subagent finished" signal, emitted by
      // observe_adjust_middleware for the task tool only. Redundant with
      // task_completed by design: if either arrives the card settles.
      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "task_activity" &&
        "tool_call_id" in event
      ) {
        const e = event as {
          type: "task_activity";
          tool_call_id: string;
          status?: string;
        };
        if (e.tool_call_id && e.status === "done") {
          updateSubtask({ id: e.tool_call_id, status: "completed" }, "result");
        }
        return;
      }

      // The model stopped on a safety finish_reason and its tool calls were
      // dropped. Nothing surfaced this, so the run simply stopped with no
      // explanation -- the user had no way to tell it apart from a hang.
      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "safety_termination"
      ) {
        const e = event as {
          type: "safety_termination";
          suppressed_tool_call_count?: number;
          reason_value?: string;
        };
        const suppressed = e.suppressed_tool_call_count ?? 0;
        toast(
          suppressed > 0
            ? `Stopped for safety (${e.reason_value ?? "unknown"}) — ${suppressed} tool call${suppressed === 1 ? "" : "s"} were not run.`
            : `Stopped for safety (${e.reason_value ?? "unknown"}).`,
        );
        return;
      }

      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "llm_retry" &&
        "message" in event &&
        typeof event.message === "string" &&
        event.message.trim()
      ) {
        const e = event as { type: "llm_retry"; message: string };
        toast(e.message);
        return;
      }

      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "task_progress"
      ) {
        const e = event as {
          type: "task_progress";
          step: number;
          total: number;
          status: string;
        };
        listeners.current.onTaskProgress?.({
          step: e.step,
          total: e.total,
          status: e.status,
        });
      }

      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "verify_result"
      ) {
        const e = event as {
          type: "verify_result";
          thread_id: string;
          ok: boolean;
          verdict: "passed" | "issues";
          routes: Array<{
            route: string;
            ok: boolean;
            status: number | null;
            notes: string;
          }>;
          console_errors_count: number;
          screenshot: string | null;
        };
        listeners.current.onVerifyResult?.({
          thread_id: e.thread_id,
          ok: e.ok,
          verdict: e.verdict,
          routes: e.routes ?? [],
          console_errors_count: e.console_errors_count ?? 0,
          screenshot: e.screenshot ?? null,
        });
      }

      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "llm_error"
      ) {
        const e = event as {
          type: "llm_error";
          error_type: string;
          reason: string;
          detail: string;
          http_status: number | null;
          code: string | null;
        };
        listeners.current.onLlmError?.({
          error_type: e.error_type ?? "Unknown",
          reason: e.reason ?? "unknown",
          detail: e.detail ?? "",
          http_status: typeof e.http_status === "number" ? e.http_status : null,
          code: typeof e.code === "string" ? e.code : null,
        });
      }
    },
    onError(error) {
      setOptimisticMessages([]);
      setOptimisticThreadId(null);
      setLiveMessagesThreadId(null);
      // Rejoin race: the run reached a terminal state (or the gateway
      // restarted and only has a store-only record) between the runs-API
      // poll and the joinStream call. Not user-facing — mark the rejoin
      // exhausted and let the active-run poll converge on the final state.
      if (rejoinStateRef.current.inFlight) {
        const status = getHttpStatus(error);
        if (status === 404 || status === 409 || isReconnectNoise(error)) {
          rejoinStateRef.current.exhausted = true;
          console.debug("[useStream] rejoin: run no longer joinable", error);
          return;
        }
      }
      // Batch 2C.3: ConnectionError (TypeError: Failed to fetch / NetworkError
      // when attempting to fetch resource) means the SSE stream was already
      // torn down by the backend before the SDK could rejoin it. This is
      // expected when the user navigates away mid-stream and comes back; the
      // underlying run state is intact and the next user action will start a
      // fresh stream. Surface a debug log instead of a toast so the console
      // stays clean without losing observability.
      if (isReconnectNoise(error)) {
        console.debug("[useStream] reconnect noise (expected):", error);
      } else if (getHttpStatus(error) === 402) {
        // Credit wall (daily_limit_reached). Show the actionable, graceful
        // message instead of a raw "Request failed" — point the user at the
        // in-app request path (Settings → Usage) and the daily reset.
        const serverMsg = getStreamErrorMessage(error);
        const friendly =
          serverMsg &&
          serverMsg !== "Request failed." &&
          /limit|credit|token/i.test(serverMsg)
            ? serverMsg
            : "You've reached today's usage limit. Open Settings → account to request more — it resets at midnight UTC.";
        toast.error(friendly, { duration: 8000 });
      } else {
        toast.error(getStreamErrorMessage(error));
      }
      pendingUsageBaselineMessageIdsRef.current = new Set(
        messagesRef.current
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
      if (threadIdRef.current && !isMock) {
        void queryClient.invalidateQueries({
          queryKey: threadTokenUsageQueryKey(threadIdRef.current),
        });
      }
    },
    onFinish(state) {
      listeners.current.onFinish?.(state.values);
      pendingUsageBaselineMessageIdsRef.current = new Set(
        messagesRef.current
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      void queryClient.invalidateQueries({
        queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      });
      if (threadIdRef.current && !isMock) {
        void queryClient.invalidateQueries({
          queryKey: threadTokenUsageQueryKey(threadIdRef.current),
        });
      }
      recordCompletion(
        threadIdRef.current,
        null,
        "success",
        messagesRef.current.length,
      );
    },
  });

  // Observability: record every isLoading transition. This effect runs only
  // when the boolean flips, not on every render — a single-record-per-change
  // signal that we can replay to see whether the SDK ever cleared the
  // loading flag after the user reported a freeze.
  useEffect(() => {
    recordManagerState(onStreamThreadId, thread.isLoading, 0);
  }, [onStreamThreadId, thread.isLoading]);

  // --- Live-run rejoin ------------------------------------------------------
  // If the server reports a pending/running run while no stream is attached
  // (page refresh, dropped SSE connection, run started from another client),
  // rejoin its stream. joinStream sends Last-Event-ID "-1" so the gateway's
  // stream bridge replays the full buffered event log before going live.
  const activeRun = useActiveRun(onStreamThreadId ?? undefined, {
    enabled: !isMock,
    isStreamLoading: thread.isLoading,
  });
  const joinStreamRef = useRef(thread.joinStream);
  joinStreamRef.current = thread.joinStream;
  const isStreamLoadingRef = useRef(thread.isLoading);
  isStreamLoadingRef.current = thread.isLoading;

  useEffect(() => {
    if (!activeRun || isMock || !onStreamThreadId) {
      return;
    }
    if (isStreamLoadingRef.current) {
      recordRejoinAttempt(
        onStreamThreadId,
        activeRun.run_id,
        rejoinStateRef.current.attempts,
        "scheduled",
        "skipped:isLoading",
      );
      return;
    }
    const state = rejoinStateRef.current;
    if (state.runId !== activeRun.run_id) {
      // New run — fresh retry budget.
      state.runId = activeRun.run_id;
      state.attempts = 0;
      state.exhausted = false;
    }
    if (
      state.inFlight ||
      state.exhausted ||
      state.attempts >= MAX_REJOIN_ATTEMPTS
    ) {
      recordRejoinAttempt(
        onStreamThreadId,
        activeRun.run_id,
        state.attempts,
        state.runId !== activeRun.run_id ? "activeRun-changed" : "scheduled",
        state.exhausted || state.attempts >= MAX_REJOIN_ATTEMPTS
          ? "skipped:exhausted"
          : "skipped:exhausted",
      );
      return;
    }
    state.inFlight = true;
    state.attempts += 1;
    recordRejoinAttempt(
      onStreamThreadId,
      activeRun.run_id,
      state.attempts,
      state.attempts === 1 ? "activeRun-changed" : "scheduled",
      "started",
    );
    const joinedAt = Date.now();
    void joinStreamRef
      .current(activeRun.run_id)
      .catch((error: unknown) => {
        console.debug("[useStream] rejoin attempt failed:", error);
        recordReconnect(onStreamThreadId, activeRun.run_id, "failed");
      })
      .finally(() => {
        state.inFlight = false;
        // A join that streamed for a while was a healthy session that ended
        // or dropped — restore the retry budget so a later disconnect on the
        // same long run can still rejoin. Only immediate failures burn it.
        if (Date.now() - joinedAt > REJOIN_HEALTHY_SESSION_MS) {
          state.attempts = 0;
        }
        recordReconnect(onStreamThreadId, activeRun.run_id, "joined");
        // Re-ask the server regardless of how the join ended so subtask
        // pills and the composer converge on the run's true state.
        void queryClient.invalidateQueries({
          queryKey: activeRunQueryKey(onStreamThreadId),
        });
      });
  }, [activeRun, isMock, onStreamThreadId, queryClient]);

  // --- Stream stall watchdog -------------------------------------------------
  // Detects when the SSE connection is supposedly open (isLoading=true) but
  // no transport bytes or SDK events have arrived for longer than the stall
  // threshold. When detected, the verify-then-act recovery runs: we ask the
  // server what the run's true state is, and either teardown locally (if the
  // run reached terminal state and the stream missed its end event) or
  // force-stop the local reader and let the rejoin effect pick up recovery.
  //
  // Lifecycle invariants (Phase 2 rewire):
  //   - Arms on ``thread.isLoading`` alone — no ``activeRun`` required. The
  //     primary-path freeze (stream wedges in the first 60s before the
  //     active-run poll has data) is what this fixes.
  //   - Cooldown-based re-fire: a sustained stall can be recovered more than
  //     once per run, but not in a tight loop.
  //   - Single interval per mount; cleared on every state transition that
  //     should disarm (stream closed, unmount, thread switch).
  //   - Never calls joinStream directly; the existing rejoin effect owns
  //     that path, so we cannot create duplicate reconnects.
  const watchdogIntervalRef = useRef<ReturnType<typeof setInterval> | null>(
    null,
  );
  const watchdogLastFireAtRef = useRef<number | null>(null);
  const watchdogVerifyingRef = useRef<boolean>(false);
  const stopStreamRef = useRef(thread.stop);
  stopStreamRef.current = thread.stop;
  const queryClientForWatchdogRef = useRef(queryClient);
  queryClientForWatchdogRef.current = queryClient;
  const threadIdForWatchdogRef = useRef(onStreamThreadId);
  threadIdForWatchdogRef.current = onStreamThreadId;

  // Reset streamRunIdRef when the user switches threads so the watchdog
  // doesn't trace a previous run's identity onto the new view.
  useEffect(() => {
    return () => {
      streamRunIdRef.current = null;
      watchdogLastFireAtRef.current = null;
    };
  }, [onStreamThreadId]);

  useEffect(() => {
    const disarmed = () => {
      if (watchdogIntervalRef.current !== null) {
        clearInterval(watchdogIntervalRef.current);
        watchdogIntervalRef.current = null;
      }
    };

    const arm = shouldArmWatchdog({
      isMock: Boolean(isMock),
      threadId: threadIdForWatchdogRef.current ?? null,
      isLoading: thread.isLoading,
      intervalAlreadyArmed: Boolean(watchdogIntervalRef.current),
    });
    if (!arm.arm) {
      if (
        arm.reason === "not-loading" ||
        arm.reason === "mock" ||
        arm.reason === "no-thread"
      ) {
        // Disarm on transitions to a state where the watchdog is no longer
        // applicable. Already-armed is fine — the interval is already
        // running, nothing to do.
        disarmed();
      }
      return;
    }

    const STALL_THRESHOLD_MS = 45_000;
    const SDK_FALLBACK_THRESHOLD_MS = 120_000;
    const CHECK_INTERVAL_MS = 5_000;
    const COOLDOWN_MS = STALL_THRESHOLD_MS;

    watchdogIntervalRef.current = setInterval(() => {
      // Two activity signals feed the watchdog:
      //   1. ``lastEventAtRef.current`` — SDK-level (onLangChainEvent fired).
      //   2. ``getStreamLiveness(threadId).lastByteAt`` — transport-level
      //      (any byte, including heartbeat comment frames the SDK drops).
      // The transport signal is strictly stronger: a stream alive at the
      // byte level must not be force-stopped just because no SDK event has
      // fired yet (e.g. a long-running tool with no intermediate events).
      const sdkLastAt = lastEventAtRef.current;
      const transportLastAt = getStreamLiveness(
        threadIdForWatchdogRef.current,
      ).lastByteAt;
      const now =
        typeof performance !== "undefined" ? performance.now() : Date.now();
      const decision = evaluateWatchdog({
        isLoading: thread.isLoading,
        lastEventAtMs: sdkLastAt,
        transportLastByteAtMs: transportLastAt,
        nowMs: now,
        thresholdMs: STALL_THRESHOLD_MS,
        sdkFallbackThresholdMs: SDK_FALLBACK_THRESHOLD_MS,
        lastFireAtMs: watchdogLastFireAtRef.current,
        cooldownMs: COOLDOWN_MS,
      });
      if (!decision.fire) return;

      // Cooldown elapsed → record fire and proceed.
      const firedAt = now;
      watchdogLastFireAtRef.current = firedAt;
      recordStallWatchdog(
        threadIdForWatchdogRef.current,
        streamRunIdRef.current,
        sdkLastAt ?? transportLastAt ?? 0,
        now,
        "force-stop",
      );
      // Verify-then-act: ask the server the run's true state before doing
      // anything destructive. If the run already reached terminal state,
      // the stream simply missed its end event — we just teardown locally.
      // If the run is still running server-side, we force-stop the local
      // reader and let the existing rejoin effect reattach.
      void (async () => {
        if (watchdogVerifyingRef.current) return;
        watchdogVerifyingRef.current = true;
        try {
          const threadId = threadIdForWatchdogRef.current;
          if (!threadId) return;
          let runs: Array<{ run_id: string; status: string }> | null = null;
          try {
            const result = await getAPIClient().runs.list(threadId);
            runs = (result ?? []) as Array<{
              run_id: string;
              status: string;
            }>;
          } catch {
            runs = null;
          }
          const outcome = classifyVerifyOutcome({
            runs,
            expectedRunId: streamRunIdRef.current,
          });
          recordRecovery(
            threadId,
            streamRunIdRef.current ?? "",
            "watchdog-force-stop",
            rejoinStateRef.current.attempts,
          );
          if (
            outcome.kind === "run-terminal" ||
            outcome.kind === "run-missing"
          ) {
            // The stream missed its end event — local teardown only. Do NOT
            // call thread.stop() (SDK will reject if the stream is already
            // closed). Just clear optimistic state and let the next
            // active-run poll converge to idle.
            try {
              void queryClientForWatchdogRef.current.invalidateQueries({
                queryKey: activeRunQueryKey(threadId),
              });
              void queryClientForWatchdogRef.current.invalidateQueries({
                queryKey: threadTokenUsageQueryKey(threadId),
              });
            } catch {
              // best-effort
            }
          } else {
            // Run is still running server-side: force-stop the local reader
            // and let the rejoin effect reattach (with full replay).
            try {
              void stopStreamRef.current();
            } catch {
              // best-effort
            }
            try {
              void queryClientForWatchdogRef.current.invalidateQueries({
                queryKey: activeRunQueryKey(threadId),
              });
            } catch {
              // best-effort
            }
          }
        } finally {
          watchdogVerifyingRef.current = false;
        }
      })();
    }, CHECK_INTERVAL_MS);

    return disarmed;
  }, [isMock, thread.isLoading, onStreamThreadId]);

  // Cleanup recorder: fires exactly once when the hook tears down.
  useEffect(() => {
    return () => {
      recordCleanup(
        onStreamThreadId,
        "unmount",
        watchdogIntervalRef.current !== null ? 1 : 0,
      );
      if (watchdogIntervalRef.current !== null) {
        clearInterval(watchdogIntervalRef.current);
        watchdogIntervalRef.current = null;
      }
    };
  }, [onStreamThreadId]);

  const hasVisibleStreamState =
    Boolean(threadId) || liveMessagesThreadId === currentViewThreadId;
  const persistedMessages = useMemo(
    () => (hasVisibleStreamState ? thread.messages : []),
    [hasVisibleStreamState, thread.messages],
  );
  const visibleHistory = useMemo(
    () => (threadId ? history : []),
    [history, threadId],
  );
  const humanMessageCount = persistedMessages.filter(
    (m) => m.type === "human",
  ).length;
  const latestMessageCountsRef = useRef({ humanMessageCount });
  const sendInFlightRef = useRef(false);
  const messagesRef = useRef<Message[]>([]);
  const summarizedRef = useRef<Set<string>>(null);
  // Track human message count before sending to prevent clearing optimistic
  // messages before the server's human message arrives (e.g. when AI messages
  // from "messages-tuple" events arrive before the input human message from
  // "values" events).
  const prevHumanMsgCountRef = useRef(humanMessageCount);

  latestMessageCountsRef.current = { humanMessageCount };
  summarizedRef.current ??= new Set<string>();

  // Reset thread-local pending UI state when switching between threads so
  // optimistic messages and in-flight guards do not leak across chat views.
  useEffect(() => {
    startedRef.current = false;
    sendInFlightRef.current = false;
    messagesRef.current = [];
    summarizedRef.current = new Set<string>();
    pendingUsageBaselineMessageIdsRef.current = new Set();
    prevHumanMsgCountRef.current =
      latestMessageCountsRef.current.humanMessageCount;
  }, [threadId]);

  useEffect(() => {
    if (optimisticThreadId && optimisticThreadId !== currentViewThreadId) {
      setOptimisticMessages([]);
      setOptimisticThreadId(null);
    }
    if (liveMessagesThreadId && liveMessagesThreadId !== currentViewThreadId) {
      setLiveMessagesThreadId(null);
    }
  }, [currentViewThreadId, liveMessagesThreadId, optimisticThreadId]);

  // When streaming starts without a baseline (e.g. reconnection, run started
  // from another client, or page reload mid-stream), snapshot the current
  // messages so only *new* messages are treated as "pending" for token usage.
  useEffect(() => {
    if (
      thread.isLoading &&
      pendingUsageBaselineMessageIdsRef.current.size === 0
    ) {
      pendingUsageBaselineMessageIdsRef.current = new Set(
        persistedMessages
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
    }
  }, [persistedMessages, thread.isLoading]);

  // Clear optimistic when server messages arrive.
  // For messages with a human optimistic message, wait until the server's
  // human message has arrived to avoid clearing before the input message
  // appears in the stream (the input message may arrive via "values" events
  // after individual "messages-tuple" events for AI messages).
  const optimisticMessageCount = optimisticMessages.length;
  const hasHumanOptimistic = optimisticMessages.some((m) => m.type === "human");
  useEffect(() => {
    if (optimisticMessageCount === 0) return;

    const newHumanMsgArrived = humanMessageCount > prevHumanMsgCountRef.current;

    if (!hasHumanOptimistic || newHumanMsgArrived) {
      setOptimisticMessages([]);
      setOptimisticThreadId(null);
    }
  }, [hasHumanOptimistic, humanMessageCount, optimisticMessageCount]);

  const sendMessage = useCallback(
    async (
      threadId: string,
      message: PromptInputMessage,
      extraContext?: Record<string, unknown>,
      options?: SendMessageOptions,
    ) => {
      if (sendInFlightRef.current) {
        return;
      }
      sendInFlightRef.current = true;

      const text = message.text.trim();

      // Capture the current human message count before showing optimistic
      // messages so we can wait for the server's copy of the user input.
      prevHumanMsgCountRef.current = humanMessageCount;
      pendingUsageBaselineMessageIdsRef.current = new Set(
        persistedMessages
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );

      // Build optimistic files list with uploading status
      const optimisticFiles: FileInMessage[] = (message.files ?? []).map(
        (f) => ({
          filename: f.filename ?? "",
          size: 0,
          status: "uploading" as const,
        }),
      );

      const hideFromUI = options?.additionalKwargs?.hide_from_ui === true;
      const optimisticAdditionalKwargs = {
        ...options?.additionalKwargs,
        ...(optimisticFiles.length > 0 ? { files: optimisticFiles } : {}),
      };

      const newOptimistic: Message[] = [];
      if (!hideFromUI) {
        newOptimistic.push({
          type: "human",
          id: `opt-human-${Date.now()}`,
          content: text ? [{ type: "text", text }] : "",
          additional_kwargs: optimisticAdditionalKwargs,
        });
      }

      if (optimisticFiles.length > 0 && !hideFromUI) {
        // Mock AI message while files are being uploaded
        newOptimistic.push({
          type: "ai",
          id: `opt-ai-${Date.now()}`,
          content: t.uploads.uploadingFiles,
          additional_kwargs: { element: "task" },
        });
      }
      setOptimisticThreadId(threadId);
      setLiveMessagesThreadId(threadId);
      setOptimisticMessages(newOptimistic);

      listeners.current.onSend?.(threadId);

      let uploadedFileInfo: UploadedFileInfo[] = [];

      try {
        // Upload files first if any
        if (message.files && message.files.length > 0) {
          setIsUploading(true);
          try {
            const filePromises = message.files.map((fileUIPart) =>
              promptInputFilePartToFile(fileUIPart),
            );

            const conversionResults = await Promise.all(filePromises);
            const files = conversionResults.filter(
              (file): file is File => file !== null,
            );
            const failedConversions = conversionResults.length - files.length;

            if (failedConversions > 0) {
              throw new Error(
                `Failed to prepare ${failedConversions} attachment(s) for upload. Please retry.`,
              );
            }

            if (!threadId) {
              throw new Error("Thread is not ready for file upload.");
            }

            if (files.length > 0) {
              const uploadResponse = await uploadFiles(threadId, files);
              uploadedFileInfo = uploadResponse.files;

              // Update optimistic human message with uploaded status + paths
              const uploadedFiles: FileInMessage[] = uploadedFileInfo.map(
                (info) => ({
                  filename: info.filename,
                  size: info.size,
                  path: info.virtual_path,
                  status: "uploaded" as const,
                }),
              );
              setOptimisticMessages((messages) => {
                if (messages.length > 1 && messages[0]) {
                  const humanMessage: Message = messages[0];
                  return [
                    {
                      ...humanMessage,
                      additional_kwargs: { files: uploadedFiles },
                    },
                    ...messages.slice(1),
                  ];
                }
                return messages;
              });
            }
          } catch (error) {
            const errorMessage =
              error instanceof Error
                ? error.message
                : "Failed to upload files.";
            toast.error(errorMessage);
            setOptimisticMessages([]);
            setOptimisticThreadId(null);
            setLiveMessagesThreadId(null);
            throw error;
          } finally {
            setIsUploading(false);
          }
        }

        // Build files metadata for submission (included in additional_kwargs)
        const filesForSubmit: FileInMessage[] = uploadedFileInfo.map(
          (info) => ({
            filename: info.filename,
            size: info.size,
            path: info.virtual_path,
            status: "uploaded" as const,
          }),
        );

        await thread.submit(
          {
            messages: [
              {
                type: "human",
                content: [
                  {
                    type: "text",
                    text,
                  },
                ],
                additional_kwargs: {
                  ...options?.additionalKwargs,
                  ...(filesForSubmit.length > 0
                    ? { files: filesForSubmit }
                    : {}),
                },
              },
            ],
          },
          {
            threadId: threadId,
            streamSubgraphs: true,
            streamResumable: true,
            config: {
              // Graph SUPER-steps, not agent turns: the lead agent's ~15
              // middlewares make each turn cost ~7-10 steps, so 1000 capped
              // real orchestrations at ~100 turns (run 2ea08475 died
              // mid-progress). 5000 ≈ 500 turns; still a runaway backstop.
              recursion_limit: 5000,
            },
            context: {
              ...extraContext,
              ...context,
              thinking_enabled: context.mode !== "flash",
              is_plan_mode: context.mode === "pro" || context.mode === "ultra",
              // Subagents available in pro + ultra (not just ultra) so delegation
              // can fire from the get-go on substantial tasks.
              subagent_enabled:
                context.mode === "pro" || context.mode === "ultra",
              reasoning_effort:
                context.reasoning_effort ??
                (context.mode === "ultra"
                  ? "high"
                  : context.mode === "pro"
                    ? "medium"
                    : context.mode === "thinking"
                      ? "low"
                      : undefined),
              thread_id: threadId,
            },
          },
        );
        void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
        void queryClient.invalidateQueries({
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
        });
      } catch (error) {
        setOptimisticMessages([]);
        setOptimisticThreadId(null);
        setLiveMessagesThreadId(null);
        setIsUploading(false);
        // A 409 "thread already has an active run" is an expected concurrency
        // conflict (the user sent a message while the agent was still working),
        // not a failure to surface as a red error overlay. Show a calm hint and
        // swallow it — the optimistic message was already rolled back above.
        if (getHttpStatus(error) === 409) {
          toast.info(t.common.agentBusy);
          return;
        }
        throw error;
      } finally {
        sendInFlightRef.current = false;
      }
    },
    [
      thread,
      t.uploads.uploadingFiles,
      t.common.agentBusy,
      context,
      queryClient,
      humanMessageCount,
      persistedMessages,
    ],
  );

  // Cache the latest thread messages in a ref to compare against incoming history messages for deduplication,
  // and to allow access to the full message list in onUpdateEvent without causing re-renders.
  if (persistedMessages.length >= messagesRef.current.length) {
    messagesRef.current = persistedMessages;
  }

  const visibleOptimisticMessages = getVisibleOptimisticMessages(
    optimisticThreadId === currentViewThreadId ? optimisticMessages : [],
    prevHumanMsgCountRef.current,
    humanMessageCount,
  );

  const mergedMessages = mergeMessages(
    visibleHistory,
    persistedMessages,
    visibleOptimisticMessages,
  );
  recordStateMerge(
    threadId ?? null,
    activeRun?.run_id ?? null,
    mergedMessages.length,
    mergedMessages[mergedMessages.length - 1]?.id ?? null,
  );
  const pendingUsageMessages = thread.isLoading
    ? getMessagesAfterBaseline(
        persistedMessages,
        pendingUsageBaselineMessageIdsRef.current,
      )
    : [];

  // Merge history, live stream, and optimistic messages for display
  // History messages may overlap with thread.messages; thread.messages take precedence
  const mergedThread = {
    ...thread,
    values: hasVisibleStreamState ? thread.values : EMPTY_THREAD_VALUES,
    messages: mergedMessages,
  } as typeof thread;

  // Stop = hard cancel. ``thread.stop()`` aborts the client stream; the run was
  // created with ``onDisconnect: "cancel"`` so the backend run is cancelled too.
  // As a belt-and-suspenders guarantee that the agent actually halts when the
  // user asks (and isn't left running by a resumable-stream race), also issue an
  // Stop is a state machine now (Phase 4). The previous implementation had no
  // timeout — if the gateway hung, Stop silently blocked the UI. We bound
  // every step with a 5s timeout; if any step fails or hangs, we fall
  // through to forceDisconnect() so the user is never trapped.
  const stopStartedAtRef = useRef<number | null>(null);
  const [stopState, setStopState] = useState<
    "idle" | "stopping" | "stopped" | "force-disconnected"
  >("idle");
  const [dismissedRunId, setDismissedRunId] = useState<string | null>(null);
  const stopTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearStopTimeout = useCallback(() => {
    if (stopTimeoutRef.current !== null) {
      clearTimeout(stopTimeoutRef.current);
      stopTimeoutRef.current = null;
    }
  }, []);

  const escapeComposer = useCallback(() => {
    // User has given up on a run (Stop timeout → Force Disconnect). Clear
    // optimistic state and dismiss this run so the composer becomes usable
    // again. The server may still be running the run; the next send will
    // hit a 409 (handled by the existing "agent busy" toast).
    clearStopTimeout();
    setDismissedRunId((current) => current ?? streamRunIdRef.current);
    setOptimisticMessages([]);
    setOptimisticThreadId(null);
    setLiveMessagesThreadId(null);
    try {
      void queryClient.invalidateQueries({
        queryKey: activeRunQueryKey(threadIdRef.current),
      });
    } catch {
      // best-effort
    }
    setStopState("idle");
    recordRecovery(
      threadIdRef.current,
      streamRunIdRef.current ?? "",
      "stop-timeout-forced",
      rejoinStateRef.current.attempts,
    );
  }, [clearStopTimeout, queryClient, rejoinStateRef]);

  const forceDisconnect = useCallback(() => {
    // Purely local — server run is left running, but this client is no
    // longer subscribed. Escape hatch for "the gateway is unreachable".
    clearStopTimeout();
    try {
      void thread.stop();
    } catch {
      // best-effort
    }
    try {
      queryClient.removeQueries({
        queryKey: activeRunQueryKey(threadIdRef.current),
      });
    } catch {
      // best-effort
    }
    setDismissedRunId((current) => current ?? streamRunIdRef.current);
    setStopState("force-disconnected");
    recordRecovery(
      threadIdRef.current,
      streamRunIdRef.current ?? "",
      "force-disconnect",
      rejoinStateRef.current.attempts,
    );
  }, [clearStopTimeout, queryClient, rejoinStateRef, thread]);

  const stopRun = useCallback(async () => {
    if (!thread.isLoading && !activeRun) {
      return; // nothing to stop
    }
    stopStartedAtRef.current = Date.now();
    setStopState("stopping");

    const runWithTimeout = <T>(
      p: Promise<T>,
      ms: number,
    ): Promise<T | typeof TIMEOUT_SENTINEL> =>
      Promise.race<T | typeof TIMEOUT_SENTINEL>([
        p,
        new Promise<typeof TIMEOUT_SENTINEL>((resolve) => {
          stopTimeoutRef.current = setTimeout(() => {
            resolve(TIMEOUT_SENTINEL);
          }, ms);
        }),
      ]);

    let serverAcked = false;
    try {
      const stopResult = await runWithTimeout(thread.stop(), 5_000);
      if (stopResult === TIMEOUT_SENTINEL) {
        // thread.stop() hung — go to force disconnect.
        forceDisconnect();
        return;
      }
    } catch {
      // SDK rejected the stop — still try to cancel server-side.
    }
    clearStopTimeout();

    if (threadId && !isMock) {
      try {
        const client = getAPIClient();
        const cancelResult = await runWithTimeout(
          (async () => {
            const runs = await client.runs.list(threadId);
            await Promise.all(
              runs
                .filter((r) => r.status === "pending" || r.status === "running")
                .map((r) =>
                  client.runs
                    .cancel(threadId, r.run_id, false, "interrupt")
                    .catch(() => undefined),
                ),
            );
            return true;
          })(),
          5_000,
        );
        serverAcked = cancelResult === true;
      } catch {
        serverAcked = false;
      }
    } else {
      serverAcked = true; // mock mode — nothing server-side to ack
    }

    const transition = nextStopState(
      stopState,
      "tick",
      stopStartedAtRef.current ? Date.now() - stopStartedAtRef.current : 0,
      serverAcked,
    );
    setStopState(transition.next);
    if (transition.outcome.kind === "force-disconnected") {
      escapeComposer();
    }
  }, [
    activeRun,
    clearStopTimeout,
    escapeComposer,
    forceDisconnect,
    isMock,
    stopState,
    thread,
    threadId,
  ]);

  // Cleanup stop timeout on unmount.
  useEffect(() => {
    return () => {
      clearStopTimeout();
    };
  }, [clearStopTimeout]);

  return {
    thread: mergedThread,
    pendingUsageMessages,
    sendMessage,
    isUploading,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
    stopRun,
    forceDisconnect,
    escapeComposer,
    stopState,
    dismissedRunId,
  } as const;
}

type ThreadHistoryOptions = {
  enabled?: boolean;
};

export function useThreadHistory(
  threadId: string,
  { enabled = true }: ThreadHistoryOptions = {},
) {
  const runs = useThreadRuns(threadId, { enabled });
  const threadIdRef = useRef(threadId);
  const runsRef = useRef(runs.data ?? []);
  const indexRef = useRef(-1);
  const loadingRef = useRef(false);
  const pendingLoadRef = useRef(false);
  const loadingRunIdRef = useRef<string | null>(null);
  const loadedRunIdsRef = useRef<Set<string>>(new Set());
  const runBeforeSeqRef = useRef<Map<string, number>>(new Map());
  const loadGenerationRef = useRef(0);
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);

  const loadMessages = useCallback(async () => {
    if (!enabled) {
      return;
    }
    const loadGeneration = loadGenerationRef.current;
    if (loadingRef.current) {
      const pendingRunIndex = findLatestUnloadedRunIndex(
        runsRef.current,
        loadedRunIdsRef.current,
      );
      const pendingRun = runsRef.current[pendingRunIndex];
      if (pendingRun && pendingRun.run_id !== loadingRunIdRef.current) {
        pendingLoadRef.current = true;
      }
      return;
    }
    if (runsRef.current.length === 0) {
      return;
    }

    loadingRef.current = true;
    setLoading(true);

    try {
      let consecutiveEmptyLoads = 0;
      do {
        pendingLoadRef.current = false;

        const nextRunIndex = findLatestUnloadedRunIndex(
          runsRef.current,
          loadedRunIdsRef.current,
        );
        indexRef.current = nextRunIndex;

        const run = runsRef.current[nextRunIndex];
        if (!run) {
          indexRef.current = -1;
          return;
        }

        const requestThreadId = threadIdRef.current;
        loadingRunIdRef.current = run.run_id;
        const beforeSeq = runBeforeSeqRef.current.get(run.run_id);
        const url = buildRunMessagesUrl(
          getBackendBaseURL(),
          requestThreadId,
          run.run_id,
          beforeSeq,
        );
        const result: RunMessagesPageResponse = await fetch(url, {
          method: "GET",
          headers: {
            "Content-Type": "application/json",
          },
          credentials: "include",
        }).then((res) => {
          // Without this an error body parses fine, `result.data.filter`
          // throws into the console.error catch below, and -- because that
          // happens BEFORE loadedRunIdsRef.add(run.run_id) -- hasUnloadedRuns
          // stays true. "Load more" then stays clickable forever, doing
          // nothing, with no feedback beyond a console line.
          if (!res.ok) {
            throw new Error(`Failed to load run messages: HTTP ${res.status}`);
          }
          return res.json();
        });
        if (
          loadGenerationRef.current !== loadGeneration ||
          threadIdRef.current !== requestThreadId
        ) {
          return;
        }
        const _messages = result.data
          .filter((m) => !m.metadata.caller?.startsWith("middleware:"))
          .map((m) => m.content);
        setMessages((prev) =>
          dedupeMessagesByIdentity([..._messages, ...prev]),
        );
        const nextBeforeSeq = getNextRunMessagesBeforeSeq(result);
        if (typeof nextBeforeSeq === "number") {
          runBeforeSeqRef.current.set(run.run_id, nextBeforeSeq);
          pendingLoadRef.current = true;
        } else if (nextBeforeSeq === undefined) {
          console.warn(
            `Run ${run.run_id} returned has_more without message seq values; leaving it pending for retry.`,
          );
        } else {
          runBeforeSeqRef.current.delete(run.run_id);
          loadedRunIdsRef.current.add(run.run_id);
          if (
            shouldAutoContinueOnEmptyRun(
              _messages.length,
              consecutiveEmptyLoads,
            )
          ) {
            consecutiveEmptyLoads += 1;
            pendingLoadRef.current = true;
          } else {
            consecutiveEmptyLoads = 0;
          }
        }
        indexRef.current = findLatestUnloadedRunIndex(
          runsRef.current,
          loadedRunIdsRef.current,
        );
      } while (pendingLoadRef.current);
    } catch (err) {
      console.error(err);
    } finally {
      if (loadGenerationRef.current === loadGeneration) {
        loadingRef.current = false;
        loadingRunIdRef.current = null;
        setLoading(false);
      }
    }
  }, [enabled]);
  useEffect(() => {
    const threadChanged = threadIdRef.current !== threadId;
    threadIdRef.current = threadId;

    if (!enabled || threadChanged) {
      loadGenerationRef.current += 1;
      runsRef.current = [];
      indexRef.current = -1;
      pendingLoadRef.current = false;
      loadingRunIdRef.current = null;
      loadedRunIdsRef.current = new Set();
      runBeforeSeqRef.current = new Map();
      loadingRef.current = false;
      setLoading(false);
      setMessages([]);
    }

    if (!enabled) {
      return;
    }

    if (runs.data && runs.data.length > 0) {
      runsRef.current = runs.data ?? [];
      indexRef.current = findLatestUnloadedRunIndex(
        runs.data,
        loadedRunIdsRef.current,
      );
    }
    loadMessages().catch(() => {
      toast.error("Failed to load thread history.");
    });
  }, [enabled, threadId, runs.data, loadMessages]);

  const appendMessages = useCallback((_messages: Message[]) => {
    setMessages((prev) => {
      return dedupeMessagesByIdentity([...prev, ..._messages]);
    });
  }, []);
  const hasThreadId = Boolean(threadId);
  const hasUnloadedRuns = Boolean(
    runs.data?.some((run) => !loadedRunIdsRef.current.has(run.run_id)),
  );
  const isRunsLoading =
    enabled &&
    hasThreadId &&
    (runs.isLoading || (runs.isFetching && !runs.data));
  const isRunsUnresolved =
    enabled && hasThreadId && !runs.data && !runs.isError;
  const hasMore =
    enabled && hasThreadId && (indexRef.current >= 0 || hasUnloadedRuns);
  return {
    runs: runs.data,
    messages,
    loading: loading || isRunsLoading || isRunsUnresolved,
    appendMessages,
    hasMore,
    loadMore: loadMessages,
  };
}

export const INFINITE_THREADS_PAGE_SIZE = 50;

export const INFINITE_THREADS_QUERY_KEY_PREFIX = [
  "threads",
  "searchInfinite",
] as const;

type InfiniteThreadsParams = Omit<
  Parameters<ThreadsClient["search"]>[0],
  "limit" | "offset"
>;

export function getInfiniteThreadsNextPageParam(
  lastPage: AgentThread[],
  allPages: AgentThread[][],
  pageSize: number = INFINITE_THREADS_PAGE_SIZE,
): number | undefined {
  if (lastPage.length < pageSize) {
    return undefined;
  }
  return allPages.reduce((sum, page) => sum + page.length, 0);
}

export function mapInfiniteThreadsCache(
  oldData: InfiniteData<AgentThread[]> | undefined,
  mapper: (thread: AgentThread) => AgentThread,
): InfiniteData<AgentThread[]> | undefined {
  if (!oldData) {
    return oldData;
  }
  return {
    ...oldData,
    pages: oldData.pages.map((page) => page.map(mapper)),
  };
}

export function filterInfiniteThreadsCache(
  oldData: InfiniteData<AgentThread[]> | undefined,
  predicate: (thread: AgentThread) => boolean,
): InfiniteData<AgentThread[]> | undefined {
  if (!oldData) {
    return oldData;
  }
  return {
    ...oldData,
    pages: oldData.pages.map((page) => page.filter(predicate)),
  };
}

export function useInfiniteThreads(
  params: InfiniteThreadsParams = {
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "updated_at", "values", "metadata"],
  },
) {
  const apiClient = getAPIClient();
  return useInfiniteQuery<
    AgentThread[],
    Error,
    InfiniteData<AgentThread[]>,
    readonly unknown[],
    number
  >({
    queryKey: [...INFINITE_THREADS_QUERY_KEY_PREFIX, params],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const response = (await apiClient.threads.search<AgentThreadState>({
        ...params,
        limit: INFINITE_THREADS_PAGE_SIZE,
        offset: pageParam,
      })) as AgentThread[];
      return response;
    },
    getNextPageParam: (lastPage, allPages) =>
      getInfiniteThreadsNextPageParam(lastPage, allPages),
    refetchOnWindowFocus: false,
  });
}

export function useThreadRuns(
  threadId?: string,
  { enabled = true }: { enabled?: boolean } = {},
) {
  const apiClient = getAPIClient();
  return useQuery<Run[]>({
    queryKey: ["thread", threadId],
    queryFn: async () => {
      if (!threadId) {
        return [];
      }
      const response = await apiClient.runs.list(threadId);
      return response;
    },
    enabled: enabled && Boolean(threadId),
    refetchOnWindowFocus: false,
  });
}
const ACTIVE_RUN_POLL_INTERVAL_MS = 4_000;
const ACTIVE_RUN_POLL_INTERVAL_WHILE_STREAMING_MS = 30_000;

// Rejoin retry budget per run: joins that fail immediately (join refused,
// connection error) stop after this many tries; a join that streamed for at
// least REJOIN_HEALTHY_SESSION_MS restores the budget so long runs survive
// repeated disconnects (laptop sleep, network blips).
const MAX_REJOIN_ATTEMPTS = 3;
const REJOIN_HEALTHY_SESSION_MS = 30_000;

export function activeRunQueryKey(threadId?: string | null) {
  return ["thread", "active-run", threadId] as const;
}

function pickActiveRun(runs: Run[] | undefined): Run | null {
  if (!runs?.length) {
    return null;
  }
  return (
    runs.find((r) => r.status === "pending" || r.status === "running") ?? null
  );
}

/**
 * Pure helper for the active-run poll cadence.
 *
 * Phase 3 rewire: the poll must never be fully absent while a run exists.
 * The previous implementation disabled polling entirely while the SDK was
 * loading (``enabled && !isStreamLoading``), which meant the watchdog had
 * no ``activeRun`` data on the primary path (the first 60 s after a send).
 * The fix keeps polling on at all times, but slows it down (30 s) while
 * a stream is attached so it does not race with the live event stream.
 *
 * When no active run exists the poll is paused (false) — no need to ask
 * the server when the answer is deterministically "no run".
 */
export function activeRunPollInterval(args: {
  hasActiveRun: boolean;
  isStreamLoading: boolean;
}): number | false {
  if (!args.hasActiveRun) return false;
  return args.isStreamLoading
    ? ACTIVE_RUN_POLL_INTERVAL_WHILE_STREAMING_MS
    : ACTIVE_RUN_POLL_INTERVAL_MS;
}

/**
 * Server-truth signal for "this thread has a run executing right now".
 *
 * The SSE stream is not a reliable liveness signal: the backend keeps
 * executing after a refresh or dropped connection (runs are created with a
 * resumable stream), so `thread.isLoading === false` only means *this client*
 * is not attached. This hook asks the runs API instead and keeps polling
 * while a pending/running run exists.
 *
 * Phase 3 rewire: polling is NEVER fully disabled while a run exists. While
 * the SDK is loading, polling falls back to a slower 30 s cadence so the
 * composer and watchdog have continuous server truth even when the SDK
 * does not surface events. Refetch on visibilitychange→visible and online
 * events is handled by the caller (see ``useActiveRunWithAwareness``).
 */

/**
 * Like {@link useActiveRun}, but also reports whether the answer is *known*.
 *
 * `useActiveRun` returns `null` both when the runs list has been fetched and
 * contains nothing pending/running, and when it has never been fetched at all
 * — callers using `enabled: false` only read whatever is already cached. Those
 * two cases look identical and are not: the first is evidence, the second is
 * absence of it. Callers that draw a terminal conclusion from "no active run"
 * (see derivePendingSubtaskStatus) need to tell them apart.
 */
export function useActiveRunState(
  threadId?: string,
  {
    enabled = true,
    isStreamLoading = false,
  }: { enabled?: boolean; isStreamLoading?: boolean } = {},
): { run: Run | null; known: boolean } {
  const apiClient = getAPIClient();
  const queryClient = useQueryClient();
  const query = useQuery<Run[]>({
    queryKey: activeRunQueryKey(threadId),
    queryFn: async () => {
      if (!threadId) {
        return [];
      }
      return apiClient.runs.list(threadId);
    },
    enabled: enabled && Boolean(threadId),
    refetchOnWindowFocus: true,
    refetchInterval: (query) =>
      activeRunPollInterval({
        hasActiveRun: Boolean(pickActiveRun(query.state.data)),
        isStreamLoading,
      }),
  });
  const knownRuns = query.data !== undefined;
  // Refetch on visibility change + online/offline transitions. The
  // hook is the single place to wire this so every consumer benefits.
  useEffect(() => {
    if (!enabled || !threadId) return;
    const handleVisibilityChange = () => {
      if (
        typeof document !== "undefined" &&
        document.visibilityState === "visible"
      ) {
        void queryClient.invalidateQueries({
          queryKey: activeRunQueryKey(threadId),
        });
      }
    };
    const handleOnline = () => {
      void queryClient.invalidateQueries({
        queryKey: activeRunQueryKey(threadId),
      });
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }
    if (typeof window !== "undefined") {
      window.addEventListener("online", handleOnline);
    }
    return () => {
      if (typeof document !== "undefined") {
        document.removeEventListener(
          "visibilitychange",
          handleVisibilityChange,
        );
      }
      if (typeof window !== "undefined") {
        window.removeEventListener("online", handleOnline);
      }
    };
  }, [enabled, threadId, queryClient]);
  return useMemo(
    () => ({ run: pickActiveRun(query.data), known: knownRuns }),
    [query.data, knownRuns],
  );
}

/**
 * The active run for a thread, or null.
 *
 * Kept as the ergonomic default for the many callers that only need the run
 * itself. Anything that treats `null` as proof no run is in flight should use
 * {@link useActiveRunState} and check `known` first.
 */
export function useActiveRun(
  threadId?: string,
  options: { enabled?: boolean; isStreamLoading?: boolean } = {},
): Run | null {
  return useActiveRunState(threadId, options).run;
}

export function useThreadMetadata(
  threadId?: string | null,
  {
    enabled = true,
    isMock = false,
  }: { enabled?: boolean; isMock?: boolean } = {},
) {
  const apiClient = getAPIClient(isMock);
  return useQuery<AgentThread | null>({
    queryKey: ["thread", "metadata", threadId, isMock],
    queryFn: async () => {
      if (!threadId) {
        return null;
      }
      try {
        const response = await apiClient.threads.get(threadId);
        return response as AgentThread;
      } catch (error) {
        if (isThreadMissingError(error)) {
          return null;
        }
        throw error;
      }
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useThreadTokenUsage(
  threadId?: string | null,
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useQuery<ThreadTokenUsageResponse | null>({
    queryKey: threadTokenUsageQueryKey(threadId),
    queryFn: async () => {
      if (!threadId) {
        return null;
      }
      return fetchThreadTokenUsage(threadId);
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useDeleteThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      onRemoteDeleted,
    }: {
      threadId: string;
      onRemoteDeleted?: () => void;
    }) => {
      // One delete, not two. `apiClient.threads.delete` issues
      // DELETE /api/langgraph/threads/{id}, which next.config.js rewrites to
      // ${gateway}/api/threads/{id} -- the very route the second call hit. The
      // handler is @require_permission(..., require_existing=True), so with the
      // row already gone the second call 404'd, the mutation threw, onSuccess
      // never ran, and the thread was never evicted from the caches below. The
      // call site uses `mutate` with no onError, so the only symptom was a
      // deleted chat that stayed in the sidebar.
      await apiClient.threads.delete(threadId);
      onRemoteDeleted?.();
    },
    onSuccess(_, { threadId }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread> | undefined) => {
          if (oldData == null) {
            return oldData;
          }
          return oldData.filter((t) => t.thread_id !== threadId);
        },
      );
      queryClient.setQueriesData(
        {
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
          exact: false,
        },
        (oldData: InfiniteData<AgentThread[]> | undefined) =>
          filterInfiniteThreadsCache(oldData, (t) => t.thread_id !== threadId),
      );
    },

    onSettled() {
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      void queryClient.invalidateQueries({
        queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
      });
    },
  });
}

export function useRenameThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      title,
    }: {
      threadId: string;
      title: string;
    }) => {
      await apiClient.threads.updateState(threadId, {
        values: { title },
      });
    },
    onSuccess(_, { threadId, title }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread>) => {
          return oldData.map((t) => {
            if (t.thread_id === threadId) {
              return {
                ...t,
                values: {
                  ...t.values,
                  title,
                },
              };
            }
            return t;
          });
        },
      );
      queryClient.setQueriesData(
        {
          queryKey: INFINITE_THREADS_QUERY_KEY_PREFIX,
          exact: false,
        },
        (oldData: InfiniteData<AgentThread[]> | undefined) =>
          mapInfiniteThreadsCache(oldData, (t) =>
            t.thread_id === threadId
              ? {
                  ...t,
                  values: {
                    ...t.values,
                    title,
                  },
                }
              : t,
          ),
      );
    },
  });
}
