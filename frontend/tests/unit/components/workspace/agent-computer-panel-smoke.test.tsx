/**
 * Smoke test for the Agent Computer panel (C10 Batch 0).
 *
 * The panel had zero render coverage before its decomposition into
 * per-tab files + the WorkspaceStateProvider. This renders the real
 * component tree (panel + provider + thread context + i18n + query
 * client) to static markup and asserts the chrome and default tab
 * appear — pinning that the decomposition kept the panel renderable
 * and the provider supplies what the panel consumes.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import { AgentComputerPanel } from "@/components/workspace/agent-computer/agent-computer-panel";
import {
  WorkspaceStateProvider,
  useWorkspaceState,
} from "@/components/workspace/agent-computer/workspace-state";
import type { ThreadContextType } from "@/components/workspace/messages/context";
import { ThreadContext } from "@/components/workspace/messages/context";
import { I18nProvider } from "@/core/i18n/context";

const THREAD_ID = "smoke-thread-1";

function makeThreadContext(
  overrides: Partial<ThreadContextType> = {},
): ThreadContextType {
  return {
    // The panel only touches thread.isLoading/messages/values via props in
    // this render; the context object itself needs the stream-shaped slot.
    thread: {
      isLoading: false,
      messages: [],
      values: {},
    } as unknown as ThreadContextType["thread"],
    currentTool: null,
    taskProgress: null,
    verifyResult: null,
    llmError: null,
    activityEvents: [],
    activeWriteFilePath: null,
    ...overrides,
  };
}

function renderPanel(
  threadContext: ThreadContextType,
  todos: {
    content?: string;
    status?: "pending" | "in_progress" | "completed";
  }[] = [],
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, enabled: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <I18nProvider initialLocale="en-US">
        <ThreadContext.Provider value={threadContext}>
          <WorkspaceStateProvider threadId={THREAD_ID} todos={todos}>
            <AgentComputerPanel
              threadId={THREAD_ID}
              currentTool={null}
              isLoading={false}
              messages={[]}
              activeWriteFilePath={null}
              artifacts={[]}
              onClose={() => undefined}
            />
          </WorkspaceStateProvider>
        </ThreadContext.Provider>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("AgentComputerPanel smoke", () => {
  test("renders the panel chrome with all seven tabs", () => {
    const html = renderPanel(makeThreadContext());
    for (const tab of [
      "Files",
      "Terminal",
      "Editor",
      "Browser",
      "Activity",
      "Review",
      "Privacy",
    ]) {
      expect(html).toContain(tab);
    }
  });

  test("renders the task checklist when todos exist", () => {
    const html = renderPanel(makeThreadContext(), [
      { content: "First step", status: "completed" },
      { content: "Second step", status: "in_progress" },
    ]);
    expect(html).toContain("First step");
    expect(html).toContain("Second step");
  });

  test("provider surfaces running stream events as mergedEvents", () => {
    let captured: ReturnType<typeof useWorkspaceState> | null = null;
    function Probe() {
      captured = useWorkspaceState();
      return null;
    }
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, enabled: false } },
    });
    renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <I18nProvider initialLocale="en-US">
          <ThreadContext.Provider
            value={makeThreadContext({
              activityEvents: [
                {
                  id: "e1",
                  ts: "1",
                  type: "bash",
                  path: null,
                  summary: "npm test",
                  output: "",
                  status: "running",
                },
              ],
            })}
          >
            <WorkspaceStateProvider threadId={THREAD_ID} todos={[]}>
              <Probe />
            </WorkspaceStateProvider>
          </ThreadContext.Provider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(captured).not.toBeNull();
    expect(captured!.threadId).toBe(THREAD_ID);
    // The in-flight running event flows through the merged timeline.
    expect(captured!.mergedEvents.map((e) => e.summary)).toContain("npm test");
    expect(captured!.activityEvents).toHaveLength(1);
  });
});
