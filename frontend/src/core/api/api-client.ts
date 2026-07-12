"use client";

import { Client as LangGraphClient } from "@langchain/langgraph-sdk/client";

import { getLangGraphBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";
import {
  loadStaticDemoThread,
  loadStaticDemoThreads,
  staticDemoThreadState,
} from "../threads/static-demo";
import type { AgentThreadState } from "../threads/types";

import { isStateChangingMethod, readCsrfCookie } from "./fetcher";
import { livenessFetch } from "./stream-liveness";
import { sanitizeRunStreamOptions } from "./stream-mode";

/**
 * SDK ``onRequest`` hook that mints the ``X-CSRF-Token`` header from the
 * live ``csrf_token`` cookie just before each outbound fetch.
 *
 * Reading the cookie per-request (rather than baking it into the SDK's
 * ``defaultHeaders`` at construction) handles login / logout / password
 * change cookie rotation transparently. Both the ``/api/langgraph/*`` SDK
 * path and the direct REST endpoints in ``fetcher.ts:fetchWithAuth``
 * share :func:`readCsrfCookie` and :const:`STATE_CHANGING_METHODS` so
 * the contract stays in lockstep.
 */
function injectCsrfHeader(_url: URL, init: RequestInit): RequestInit {
  if (!isStateChangingMethod(init.method ?? "GET")) {
    return init;
  }
  const token = readCsrfCookie();
  if (!token) return init;
  const headers = new Headers(init.headers);
  if (!headers.has("X-CSRF-Token")) {
    headers.set("X-CSRF-Token", token);
  }
  return { ...init, headers };
}

export function isInactiveRunStreamError(error: unknown): boolean {
  const status =
    typeof error === "object" && error !== null
      ? Reflect.get(error, "status")
      : undefined;
  const message =
    typeof error === "string"
      ? error
      : error instanceof Error
        ? error.message
        : typeof error === "object" && error !== null
          ? String(Reflect.get(error, "message") ?? "")
          : "";

  // Match the gateway's store-only run response in
  // backend/app/gateway/routers/thread_runs.py until the API exposes a
  // structured error code for inactive run streams.
  return (
    (status === 409 || message.includes("HTTP 409")) &&
    message.includes("not active on this worker") &&
    message.includes("cannot be streamed")
  );
}

export function clearReconnectRun(
  threadId: string | null | undefined,
  runId: string,
): void {
  if (typeof window === "undefined" || !threadId) return;

  const key = `lg:stream:${threadId}`;
  try {
    const storage = window.sessionStorage;
    if (storage.getItem(key) === runId) {
      storage.removeItem(key);
    }
  } catch {
    // Ignore storage access failures so reconnect cleanup never throws.
  }
}

function createCompatibleClient(isMock?: boolean): LangGraphClient {
  if (isStaticWebsiteOnly() && !isMock) {
    return createStaticClient();
  }

  const apiUrl = getLangGraphBaseURL(isMock);
  if (typeof window !== "undefined" && process.env.NODE_ENV !== "production") {
    // Dev-only diagnostic. The previous unconditional console.log fired on every
    // client creation in production too; production stacks should not log
    // resolved base URLs (an info-disclosure footgun in error overlays).
    console.debug(`[langgraph-sdk] base URL: ${apiUrl}`);
  }
  const client = new LangGraphClient({
    apiUrl,
    onRequest: injectCsrfHeader,
    // livenessFetch timestamps every received SSE byte (including the
    // gateway's `: heartbeat` comment frames, which the SDK parser drops)
    // so the stall watchdog can tell a dead transport from a quiet agent.
    callerOptions: { fetch: livenessFetch },
  });

  const originalRunStream = client.runs.stream.bind(client.runs);
  client.runs.stream = ((threadId, assistantId, payload) =>
    originalRunStream(
      threadId,
      assistantId,
      sanitizeRunStreamOptions(payload),
    )) as typeof client.runs.stream;

  const originalJoinStream = client.runs.joinStream.bind(client.runs);
  client.runs.joinStream = async function* (threadId, runId, options) {
    try {
      yield* originalJoinStream(
        threadId,
        runId,
        sanitizeRunStreamOptions(options),
      );
    } catch (error) {
      if (isInactiveRunStreamError(error)) {
        clearReconnectRun(threadId, runId);
        return;
      }
      throw error;
    }
  } as typeof client.runs.joinStream;

  return client;
}

function createStaticClient(): LangGraphClient {
  const apiUrl =
    typeof window === "undefined"
      ? "http://localhost:3000"
      : window.location.origin;
  const client = new LangGraphClient({ apiUrl });

  client.threads.search = (async (query) => {
    return loadStaticDemoThreads(query);
  }) as typeof client.threads.search;

  client.threads.get = (async (threadId) => {
    return loadStaticDemoThread(threadId);
  }) as typeof client.threads.get;

  client.threads.getState = (async (threadId) => {
    return staticDemoThreadState(await loadStaticDemoThread(threadId));
  }) as typeof client.threads.getState;

  client.threads.getHistory = (async (threadId) => {
    return [staticDemoThreadState(await loadStaticDemoThread(threadId))];
  }) as typeof client.threads.getHistory;

  client.threads.update = (async (threadId) => {
    return loadStaticDemoThread(threadId);
  }) as typeof client.threads.update;

  client.runs.list = (async () => []) as typeof client.runs.list;
  client.runs.stream = async function* () {
    /* empty */
  } as typeof client.runs.stream;
  client.runs.joinStream = async function* () {
    /* empty */
  } as typeof client.runs.joinStream;

  return client as LangGraphClient<AgentThreadState>;
}

const _clients = new Map<string, LangGraphClient>();

/**
 * Marker attached to the SDK client so tests and devtools can identify
 * it without monkey-patching globals. Set on the same call site as the
 * construction so the value can never drift from reality.
 */
export const LANGGRAPH_SDK_CLIENT_TAG = "langgraph-sdk-client" as const;

export function getAPIClient(isMock?: boolean): LangGraphClient {
  const cacheKey = isMock ? "mock" : "default";
  let client = _clients.get(cacheKey);

  if (!client) {
    client = createCompatibleClient(isMock);
    // Tag the instance so Playwright/E2E can match on it without
    // touching internals.
    (client as unknown as Record<symbol, unknown>)[
      Symbol.for(LANGGRAPH_SDK_CLIENT_TAG)
    ] = true;
    _clients.set(cacheKey, client);
  }

  return client;
}
