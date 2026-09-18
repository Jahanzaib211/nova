"use client";

import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { ChevronUpIcon, Loader2Icon } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import {
  Conversation,
  ConversationContent,
} from "@/components/ai-elements/conversation";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import {
  buildTokenDebugSteps,
  type TokenUsageInlineMode,
} from "@/core/messages/usage-model";
import {
  extractContentFromMessage,
  extractPresentFilesFromMessage,
  extractTextFromMessage,
  getAssistantTurnCopyData,
  getAssistantTurnUsageMessages,
  getMessageGroups,
  getStreamingMessageLookup,
  hasContent,
  hasPresentFiles,
  hasReasoning,
  isAssistantMessageGroupStreaming,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import type { Subtask } from "@/core/tasks";
import {
  subtaskWriteIsNoop,
  useSubtasks,
  useUpdateSubtask,
  type SubtaskUpdateSource,
} from "@/core/tasks/context";
import {
  DERIVED_FAILURE_SETTLE_MS,
  derivedFailureHasSettled,
  derivePendingSubtaskStatus,
  findSubtaskResultMessage,
  parseSubtaskResult,
} from "@/core/tasks/subtask-result";
import type { AgentThreadState } from "@/core/threads";
import { useActiveRunState } from "@/core/threads/hooks";
import {
  recordRender,
  recordThinkingIndicator,
} from "@/core/threads/stream-trace";
import { cn } from "@/lib/utils";

import { ArtifactFileList } from "../artifacts/artifact-file-list";
import { CopyButton } from "../copy-button";
import { StreamingIndicator } from "../streaming-indicator";

import { MarkdownContent } from "./markdown-content";
import { MessageGroup } from "./message-group";
import { MessageListItem } from "./message-list-item";
import {
  MessageTokenUsageDebugList,
  MessageTokenUsageList,
} from "./message-token-usage";
import { MessageListSkeleton } from "./skeleton";
import { SubtaskCard } from "./subtask-card";

export const MESSAGE_LIST_DEFAULT_PADDING_BOTTOM = 24;

const LOAD_MORE_HISTORY_THROTTLE_MS = 400;

function LoadMoreHistoryIndicator({
  isLoading,
  hasMore,
  loadMore,
}: {
  isLoading?: boolean;
  hasMore?: boolean;
  loadMore?: () => void;
}) {
  const { t } = useI18n();
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLoadRef = useRef(0);

  const throttledLoadMore = useCallback(() => {
    if (!hasMore || isLoading) {
      return;
    }

    const now = Date.now();
    const remaining =
      LOAD_MORE_HISTORY_THROTTLE_MS - (now - lastLoadRef.current);

    if (remaining <= 0) {
      lastLoadRef.current = now;
      loadMore?.();
      return;
    }

    if (timeoutRef.current) {
      return;
    }

    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      if (!hasMore || isLoading) {
        return;
      }
      lastLoadRef.current = Date.now();
      loadMore?.();
    }, remaining);
  }, [hasMore, isLoading, loadMore]);

  useEffect(() => {
    const element = sentinelRef.current;
    if (!element || !hasMore) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          throttledLoadMore();
        }
      },
      {
        rootMargin: "120px 0px 0px 0px",
      },
    );

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [hasMore, throttledLoadMore]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  // IntersectionObserver only fires on threshold *crossings*. After a page of
  // history loads, the sentinel often stays inside the viewport (short runs
  // don't push it out), so no new intersection event ever fires and loading
  // stalls until the user jiggles the scroll. When a load finishes and the
  // sentinel is still visible, keep loading.
  useEffect(() => {
    if (isLoading || !hasMore) {
      return;
    }
    const element = sentinelRef.current;
    if (!element) {
      return;
    }
    const rect = element.getBoundingClientRect();
    const viewportHeight =
      window.innerHeight || document.documentElement.clientHeight;
    if (rect.bottom >= 0 && rect.top <= viewportHeight + 120) {
      throttledLoadMore();
    }
  }, [isLoading, hasMore, throttledLoadMore]);

  if (!hasMore && !isLoading) {
    return null;
  }

  return (
    <div ref={sentinelRef} className="flex w-full justify-center">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="text-muted-foreground hover:text-foreground rounded-full px-3"
        disabled={(isLoading ?? false) || !hasMore}
        onClick={throttledLoadMore}
      >
        {isLoading ? (
          <>
            <Loader2Icon className="mr-2 size-4 animate-spin" />
            {t.common.loading}
          </>
        ) : (
          <>
            <ChevronUpIcon className="mr-2 size-4" />
            {t.common.loadMore}
          </>
        )}
      </Button>
    </div>
  );
}

/**
 * Decide whether a derived `failed` may paint yet.
 *
 * Returns `in_progress` while the failure is still inside its settle window,
 * and schedules a re-render for when the window closes so the real answer is
 * not stuck behind a render that never comes. A status that came from a parsed
 * ToolMessage (`fromResult`) is evidence, not a guess, and passes straight
 * through.
 */
function holdDerivedFailure(
  status: Subtask["status"],
  fromResult: boolean,
  taskId: string,
  since: Map<string, number>,
  scheduleResettle: (delay: number) => void,
): Subtask["status"] {
  if (fromResult || status !== "failed") {
    since.delete(taskId);
    return status;
  }
  const now = Date.now();
  const first = since.get(taskId) ?? now;
  if (!since.has(taskId)) since.set(taskId, now);
  if (derivedFailureHasSettled(first, now)) return "failed";
  scheduleResettle(DERIVED_FAILURE_SETTLE_MS - (now - first) + 20);
  return "in_progress";
}

export function MessageList({
  className,
  threadId,
  thread,
  paddingBottom = MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
  tokenUsageInlineMode = "off",
  hasMoreHistory,
  loadMoreHistory,
  isHistoryLoading,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  paddingBottom?: number;
  tokenUsageInlineMode?: TokenUsageInlineMode;
  hasMoreHistory?: boolean;
  loadMoreHistory?: () => void;
  isHistoryLoading?: boolean;
}) {
  const { t } = useI18n();
  const [turnStartTime, setTurnStartTime] = useState<number | null>(null);
  const prevIsLoading = useRef(thread.isLoading);

  useEffect(() => {
    if (thread.isLoading && !prevIsLoading.current) {
      setTurnStartTime(Date.now());
    }
    prevIsLoading.current = thread.isLoading;
  }, [thread.isLoading]);
  const messages = thread.messages;
  const groupedMessages = getMessageGroups(messages);
  const lastHumanGroupIndex = useMemo(() => {
    for (let i = groupedMessages.length - 1; i >= 0; i--) {
      if (groupedMessages[i]?.type === "human") {
        return i;
      }
    }
    return -1;
  }, [groupedMessages]);
  const hasActiveAssistantText = useMemo(() => {
    if (lastHumanGroupIndex === -1) return false;
    return groupedMessages
      .slice(lastHumanGroupIndex)
      .some((g) => g.type === "assistant");
  }, [groupedMessages, lastHumanGroupIndex]);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(thread.isLoading);
  const updateSubtask = useUpdateSubtask();

  // Subtask state is *derived* from the message list, and that derivation
  // happens while building the message JSX below. Calling `updateSubtask`
  // straight from there writes to SubtasksProvider during MessageList's render,
  // which React rejects:
  //
  //   Cannot update a component (`SubtasksProvider`) while rendering a
  //   different component (`MessageList`)
  //
  // The derivation itself is fine where it is — it needs the same walk over
  // messages the rendering does. Only the *write* has to wait, so updates are
  // queued during render and flushed in the effect below. The queue is reset at
  // the top of every render so a re-render cannot replay stale entries.
  //
  // Flushing on every render is safe because `updateSubtask` hands back the
  // same state reference when nothing observable changed (see the identity note
  // in `core/tasks/context.tsx`), so React bails out instead of looping.
  // When a derived failure was first seen, per task. A derived `failed` is a
  // conclusion drawn from absence -- no tool result, no active run -- and the
  // two channels it reads are not synchronised, so it can be momentarily early.
  // Holding it for DERIVED_FAILURE_SETTLE_MS removes the red-then-green flash
  // without ever hiding a failure that is real: after the window it paints.
  const derivedFailureSince = useRef<Map<string, number>>(new Map());
  const [, forceResettle] = useReducer((n: number) => n + 1, 0);
  const resettleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // One timer for all held tasks: they share a window, so the earliest expiry
  // is enough to re-render and let every settled one through.
  const scheduleResettle = useCallback((delay: number) => {
    if (resettleTimer.current !== null) return;
    resettleTimer.current = setTimeout(() => {
      resettleTimer.current = null;
      forceResettle();
    }, delay);
  }, []);
  useEffect(
    () => () => {
      if (resettleTimer.current !== null) clearTimeout(resettleTimer.current);
    },
    [],
  );

  const queuedSubtaskUpdates = useRef<
    Array<[Partial<Subtask> & { id: string }, SubtaskUpdateSource]>
  >([]);
  queuedSubtaskUpdates.current = [];
  const queueSubtaskUpdate = (
    task: Partial<Subtask> & { id: string },
    source: SubtaskUpdateSource,
  ) => {
    queuedSubtaskUpdates.current.push([task, source]);
  };

  // Skip writes the provider already renders. The identity bail-out inside
  // `updateSubtask` is not sufficient by itself: under load the functional
  // updater can run against a base older than the last commit, so the same
  // accepted transition was re-issued every render until React #185 took the
  // whole route down (2026-09-18, thread with an orphaned `task` call).
  const renderedTasks = useSubtasks();
  useEffect(() => {
    for (const [task, source] of queuedSubtaskUpdates.current) {
      if (subtaskWriteIsNoop(renderedTasks[task.id], task)) continue;
      updateSubtask(task, source);
    }
  });
  // Server-truth liveness: a dropped stream must not paint still-running
  // subtasks as failed. Read-only consumer — `enabled: false` means this never
  // issues its own request, it only reads what useThreadStream's rejoin query
  // has already put in the cache under the same key. Owning a request here made
  // the list fire GET /runs on first send, before POST /runs/stream (issue
  // #2746), and made mock threads reach the real gateway.
  const { run: activeRun, known: runStateKnown } = useActiveRunState(threadId, {
    enabled: false,
    isStreamLoading: thread.isLoading,
  });
  const hasActiveRun = activeRun !== null;
  recordRender(
    threadId ?? null,
    activeRun?.run_id ?? null,
    "MessageList",
    messages[messages.length - 1]?.id ?? null,
  );
  const lastGroupIndex = groupedMessages.length - 1;
  const turnUsageMessagesByGroupIndex =
    getAssistantTurnUsageMessages(groupedMessages);
  const tokenDebugSteps = useMemo(
    () => buildTokenDebugSteps(messages, t),
    [messages, t],
  );
  const streamingMessages = useMemo(
    () =>
      getStreamingMessageLookup(
        messages,
        thread.isLoading,
        thread.getMessagesMetadata,
      ),
    [messages, thread.getMessagesMetadata, thread.isLoading],
  );

  const renderAssistantCopyButton = useCallback(
    (messages: Message[], isStreaming: boolean) => {
      const clipboardData = getAssistantTurnCopyData(messages, { isStreaming });

      if (!clipboardData) {
        return null;
      }

      return (
        <div className="mt-2 flex justify-start opacity-0 transition-opacity delay-200 duration-300 group-hover/assistant-turn:opacity-100 max-md:opacity-100">
          <CopyButton clipboardData={clipboardData} />
        </div>
      );
    },
    [],
  );

  const renderTokenUsage = useCallback(
    ({
      messages,
      turnUsageMessages,
      inlineDebug = true,
      debugMessageIds,
    }: {
      messages: Message[];
      turnUsageMessages?: Message[] | null;
      inlineDebug?: boolean;
      debugMessageIds?: string[];
    }) => {
      if (tokenUsageInlineMode === "per_turn") {
        return (
          <MessageTokenUsageList
            enabled={true}
            isLoading={thread.isLoading}
            messages={turnUsageMessages ?? []}
          />
        );
      }

      if (tokenUsageInlineMode === "step_debug" && inlineDebug) {
        const messageIds = new Set(
          debugMessageIds ??
            messages
              .filter((message) => message.type === "ai")
              .map((message) => message.id)
              .filter((id): id is string => typeof id === "string"),
        );
        return (
          <MessageTokenUsageDebugList
            enabled={true}
            isLoading={thread.isLoading}
            steps={tokenDebugSteps.filter((step) =>
              messageIds.has(step.messageId),
            )}
          />
        );
      }

      return null;
    },
    [thread.isLoading, tokenDebugSteps, tokenUsageInlineMode],
  );

  if (thread.isThreadLoading && messages.length === 0) {
    return <MessageListSkeleton />;
  }

  return (
    <Conversation
      className={cn("flex size-full flex-col justify-center", className)}
    >
      <ConversationContent className="mx-auto w-full max-w-(--container-width-md) gap-8 pt-8">
        <LoadMoreHistoryIndicator
          isLoading={isHistoryLoading}
          hasMore={hasMoreHistory}
          loadMore={loadMoreHistory}
        />
        {groupedMessages.map((group, groupIndex) => {
          const turnUsageMessages = turnUsageMessagesByGroupIndex[groupIndex];
          const groupIsLoading =
            thread.isLoading && groupIndex === lastGroupIndex;

          if (group.type === "human" || group.type === "assistant") {
            return (
              <div
                key={group.id}
                className={cn(
                  "w-full",
                  group.type === "assistant" && "group/assistant-turn",
                )}
              >
                {group.messages.map((msg) => {
                  return (
                    <MessageListItem
                      key={`${group.id}/${msg.id}`}
                      message={msg}
                      isLoading={
                        thread.isLoading &&
                        groupIndex === groupedMessages.length - 1
                      }
                      threadId={threadId}
                      showCopyButton={group.type !== "assistant"}
                      turnStartTime={
                        groupIndex === groupedMessages.length - 1
                          ? turnStartTime
                          : null
                      }
                    />
                  );
                })}
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                })}
                {group.type === "assistant" &&
                  renderAssistantCopyButton(
                    group.messages,
                    isAssistantMessageGroupStreaming(
                      group.messages,
                      streamingMessages,
                    ),
                  )}
              </div>
            );
          } else if (group.type === "assistant:clarification") {
            const message = group.messages[0];
            if (message && hasContent(message)) {
              return (
                <div key={group.id} className="w-full">
                  <MarkdownContent
                    content={extractContentFromMessage(message)}
                    isLoading={thread.isLoading}
                    rehypePlugins={rehypePlugins}
                  />
                  {renderTokenUsage({
                    messages: group.messages,
                    turnUsageMessages,
                  })}
                </div>
              );
            }
            return null;
          } else if (group.type === "assistant:present-files") {
            const files: string[] = [];
            for (const message of group.messages) {
              if (hasPresentFiles(message)) {
                const presentFiles = extractPresentFilesFromMessage(message);
                files.push(...presentFiles);
              }
            }
            return (
              <div className="w-full" key={group.id}>
                {group.messages[0] && hasContent(group.messages[0]) && (
                  <MarkdownContent
                    content={extractContentFromMessage(group.messages[0])}
                    isLoading={thread.isLoading}
                    rehypePlugins={rehypePlugins}
                    className="mb-4"
                  />
                )}
                <ArtifactFileList files={files} threadId={threadId} />
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                })}
              </div>
            );
          } else if (group.type === "assistant:subagent") {
            // A task's terminal state lives in its ToolMessage, which the
            // grouping logic may place in a DIFFERENT group (history merge
            // after rejoin). Always resolve status from the full message
            // list; the pending path only applies when no result exists
            // anywhere. `hasActiveRun` may only keep THIS turn's tasks alive
            // — a later turn's active run says nothing about older orphans.
            const groupIsCurrentTurn = groupIndex > lastHumanGroupIndex;
            const tasks = new Set<Subtask>();
            for (const message of group.messages) {
              if (message.type === "ai") {
                for (const toolCall of message.tool_calls ?? []) {
                  if (toolCall.name === "task") {
                    const taskId = toolCall.id;
                    if (!taskId) {
                      continue;
                    }
                    const resultMessage = findSubtaskResultMessage(
                      taskId,
                      messages,
                    );
                    const parsed = resultMessage
                      ? parseSubtaskResult(
                          extractTextFromMessage(resultMessage),
                          resultMessage.additional_kwargs,
                        )
                      : undefined;
                    const derived =
                      parsed?.status ??
                      derivePendingSubtaskStatus(
                        taskId,
                        messages,
                        groupIsLoading,
                        hasActiveRun && groupIsCurrentTurn,
                        runStateKnown,
                      );
                    // Hold a *derived* failure briefly before painting it. The
                    // runs cache and the task-event socket are independent, so
                    // the cache can say "no pending run" a beat before the
                    // socket delivers task_completed -- and the badge flashed
                    // red, then green. See DERIVED_FAILURE_SETTLE_MS.
                    const status = holdDerivedFailure(
                      derived,
                      parsed?.status !== undefined,
                      taskId,
                      derivedFailureSince.current,
                      scheduleResettle,
                    );
                    const task: Subtask = {
                      id: taskId,
                      subagent_type: toolCall.args.subagent_type,
                      description: toolCall.args.description,
                      prompt: toolCall.args.prompt,
                      status,
                      ...(parsed?.result !== undefined
                        ? { result: parsed.result }
                        : {}),
                      // A `failed` status has two very different origins, and
                      // saying "Subtask failed" for both misreads the second as
                      // an agent error:
                      //
                      //   parsed  — a real ToolMessage said the task failed
                      //   derived — no ToolMessage exists at all
                      //
                      // The derived case means the run was cut off before the
                      // subtask reported back; verified on a live thread where
                      // five orphaned task calls had neither a ToolMessage nor
                      // an llm.tool.result event, i.e. no result was ever
                      // produced. Calling that "failed" sends people hunting a
                      // subagent bug that is not there.
                      ...(parsed?.error !== undefined
                        ? { error: parsed.error }
                        : status === "failed"
                          ? {
                              error: parsed
                                ? t.subtasks.failed
                                : t.subtasks.interrupted,
                            }
                          : {}),
                    };
                    // Authority follows the parsed *status*, not the mere
                    // existence of a ToolMessage.
                    //
                    // `parseSubtaskResult` deliberately returns `in_progress`
                    // for a ToolMessage whose shape it does not recognise, so
                    // contract drift surfaces instead of being masked as a
                    // failure. But stamping that with "result" authority made it
                    // permanent: the FSM rejects every later derived correction,
                    // and supersede cannot help while the tool_call id is still
                    // live -- so the card spun forever with no path back.
                    // A non-terminal status is never a final answer.
                    const isTerminal =
                      task.status === "completed" || task.status === "failed";
                    queueSubtaskUpdate(
                      task,
                      parsed && isTerminal ? "result" : "derived",
                    );
                    tasks.add(task);
                  }
                }
              } else if (message.type === "tool") {
                const taskId = message.tool_call_id;
                if (taskId) {
                  const parsed = parseSubtaskResult(
                    extractTextFromMessage(message),
                    message.additional_kwargs,
                  );
                  // Same rule as above: only a terminal status may claim
                  // "result" authority and lock the row.
                  queueSubtaskUpdate(
                    { id: taskId, ...parsed },
                    parsed.status === "in_progress" ? "derived" : "result",
                  );
                }
              }
            }

            const results: React.ReactNode[] = [];
            const subagentDebugMessageIds: string[] = [];
            if (tasks.size > 0) {
              results.push(
                <div
                  key="subtask-count"
                  className="text-muted-foreground pt-2 text-sm font-normal"
                >
                  {t.subtasks.executing(tasks.size)}
                </div>,
              );
            }
            for (const message of group.messages.filter(
              (message) => message.type === "ai",
            )) {
              if (hasReasoning(message)) {
                results.push(
                  <MessageGroup
                    key={"thinking-group-" + message.id}
                    messages={[message]}
                    isLoading={groupIsLoading}
                    tokenDebugSteps={tokenDebugSteps.filter(
                      (step) => step.messageId === message.id,
                    )}
                    showTokenDebugSummaries={
                      tokenUsageInlineMode === "step_debug"
                    }
                  />,
                );
              } else if (message.id) {
                subagentDebugMessageIds.push(message.id);
              }
              const taskIds = message.tool_calls?.flatMap((toolCall) =>
                toolCall.name === "task" && toolCall.id ? [toolCall.id] : [],
              );
              for (const taskId of taskIds ?? []) {
                results.push(
                  <SubtaskCard
                    key={"task-group-" + taskId}
                    taskId={taskId}
                    isLoading={groupIsLoading}
                  />,
                );
              }
            }
            return (
              <div
                key={"subtask-group-" + group.id}
                className="relative z-1 flex flex-col gap-2"
              >
                {results}
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                  debugMessageIds: subagentDebugMessageIds,
                })}
              </div>
            );
          }
          return (
            <div key={"group-" + group.id} className="w-full">
              <MessageGroup
                messages={group.messages}
                isLoading={thread.isLoading}
                tokenDebugSteps={tokenDebugSteps.filter((step) =>
                  group.messages.some(
                    (message) => message.id === step.messageId,
                  ),
                )}
                showTokenDebugSummaries={tokenUsageInlineMode === "step_debug"}
              />
              {renderTokenUsage({
                messages: group.messages,
                turnUsageMessages,
                inlineDebug: false,
              })}
            </div>
          );
        })}
        {(() => {
          const indicatorVisible = thread.isLoading && !hasActiveAssistantText;
          if (indicatorVisible) {
            recordThinkingIndicator(
              threadId ?? null,
              activeRun?.run_id ?? null,
              true,
              thread.isLoading,
            );
          }
          return indicatorVisible ? (
            <div
              className="text-muted-foreground/70 flex w-full items-center gap-2 px-4 py-3"
              data-testid="streaming-indicator"
              aria-label="Agent is thinking"
            >
              <StreamingIndicator size="sm" />
              <span className="font-mono text-xs">
                {t.agentComputer.thinking}
              </span>
            </div>
          ) : null;
        })()}
        <div style={{ height: `${paddingBottom}px` }} />
      </ConversationContent>
    </Conversation>
  );
}
