"use client";

import { TerminalIcon } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { type PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import {
  ChatBox,
  useSpecificChatMode,
  useThreadChat,
} from "@/components/workspace/chats";
import { ExportTrigger } from "@/components/workspace/export-trigger";
import { InputBox } from "@/components/workspace/input-box";
import {
  MessageList,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
} from "@/components/workspace/messages";
import {
  ThreadContext,
  type LlmError,
  type TaskProgress,
  type VerifyResult,
} from "@/components/workspace/messages/context";
import { usePanels } from "@/components/workspace/panels/context";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { Tooltip } from "@/components/workspace/tooltip";
import { Welcome } from "@/components/workspace/welcome";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { useNotification } from "@/core/notification/hooks";
import { useLocalSettings, useThreadSettings } from "@/core/settings";
import {
  useThreadMetadata,
  useThreadStream,
  useThreadTokenUsage,
  type AgentActivityEvent,
} from "@/core/threads/hooks";
import { threadTokenUsageToTokenUsage } from "@/core/threads/token-usage";
import { textOfMessage } from "@/core/threads/utils";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export default function ChatPage() {
  const { t } = useI18n();
  const router = useRouter();
  const { threadId, setThreadId, isNewThread, setIsNewThread, isMock } =
    useThreadChat();
  const [isWelcomeMode, setIsWelcomeMode] = useState(isNewThread);
  // Cowork launcher: a new chat opened with ?skill=<name> pre-fills the composer
  // with the slash-command so the skill activates on the first message.
  const searchParamsForComposer = useSearchParams();
  const skillParam = searchParamsForComposer.get("skill");
  const promptParam = searchParamsForComposer.get("prompt");
  // Cowork can seed a skill (/name) and/or a free-text task via ?skill= / ?prompt=.
  const composerInitialValue = isNewThread
    ? (skillParam
        ? `/${skillParam} ${promptParam ?? ""}`.trimEnd() + (promptParam ? "" : " ")
        : (promptParam ?? undefined))
    : undefined;
  const [settings, setSettings] = useThreadSettings(threadId);
  const [localSettings, setLocalSettings] = useLocalSettings();
  const { tokenUsageEnabled } = useModels();
  const threadTokenUsage = useThreadTokenUsage(
    isNewThread || isMock ? undefined : threadId,
    { enabled: tokenUsageEnabled && !isMock },
  );
  const threadMetadata = useThreadMetadata(threadId, {
    enabled: !isNewThread && !isMock,
    isMock,
  });
  const backendTokenUsage = threadTokenUsageToTokenUsage(threadTokenUsage.data);
  const mountedRef = useRef(false);
  useSpecificChatMode();

  // Agent's Computer panel state
  const { agentComputerOpen, setAgentComputerOpen } = usePanels();
  const [currentTool, setCurrentTool] = useState<string | null>(null);
  const [taskProgress, setTaskProgress] = useState<TaskProgress | null>(null);
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);
  const [llmError, setLlmError] = useState<LlmError | null>(null);
  const [activityEvents, setActivityEvents] = useState<AgentActivityEvent[]>([]);
  const [activeWriteFilePath, setActiveWriteFilePath] = useState<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
  }, []);

  useEffect(() => {
    setIsWelcomeMode(isNewThread);
  }, [isNewThread]);

  // Reset all agent computer state when thread changes
  useEffect(() => {
    setCurrentTool(null);
    setTaskProgress(null);
    setVerifyResult(null);
    setLlmError(null);
    setActivityEvents([]);
    setActiveWriteFilePath(null);
  }, [threadId]);

  const { showNotification } = useNotification();

  const {
    thread,
    pendingUsageMessages,
    sendMessage,
    isUploading,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    displayThreadId: threadId,
    context: settings.context,
    isMock,
    onSend: () => {
      setIsWelcomeMode(false);
    },
    onStart: (createdThreadId) => {
      history.replaceState(null, "", `/workspace/chats/${createdThreadId}`);
      setThreadId(createdThreadId);
      setIsNewThread(false);
    },
    onFinish: (state) => {
      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages.at(-1);
        if (lastMessage) {
          const textContent = textOfMessage(lastMessage);
          if (textContent) {
            body =
              textContent.length > 200
                ? textContent.substring(0, 200) + "..."
                : textContent;
          }
        }
        showNotification(state.title, { body });
      }
    },
    onToolEnd: (event) => {
      setCurrentTool(event.name);
    },
    onTaskProgress: (progress) => {
      setTaskProgress(progress);
    },
    onVerifyResult: (event) => {
      setVerifyResult(event);
    },
    onLlmError: (event) => {
      setLlmError(event);
    },
    onToolActivity: (event) => {
      setActivityEvents((prev) => [...prev.slice(-199), event]);
      if (event.type === "write_file" && event.path) {
        setActiveWriteFilePath(event.path);
      }
    },
    onToolActivityDone: ({ id, name, output, path, cmd }) => {
      const status = output.startsWith("Error:") ? "error" : "done";
      setActivityEvents((prev) => {
        const idx = prev.findIndex((e) => e.id === id);
        if (idx >= 0) {
          // Update the existing "running" event created by on_tool_start
          return prev.map((e) => e.id === id ? { ...e, output, status } : e);
        }
        // on_tool_start never fired (common with subagents) — create the event now
        const ts = new Date().toLocaleTimeString("en-US", {
          hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit",
        });
        const newEvent: AgentActivityEvent = {
          id: id || `${name}-${Date.now()}`,
          ts, type: name, path,
          summary: name === "bash" && cmd ? `$ ${cmd.slice(0, 60)}`
            : name === "write_file" && path ? `Wrote ${path.split("/").at(-1)}`
            : name === "str_replace" && path ? `Edited ${path.split("/").at(-1)}`
            : name === "read_file" && path ? `Read ${path.split("/").at(-1)}`
            : name,
          output, status,
        };
        if (name === "write_file" && path) setActiveWriteFilePath(path);
        return [...prev.slice(-199), newEvent];
      });
    },
  });

  const hasThreadMessages = thread.messages.length > 0;

  // Auto-open Agent's Computer panel only on the transition into "loading" (a new
  // run starting), NOT on every render while loading — otherwise a manual close
  // mid-build instantly re-opens (panel "not collapsible during the build").
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    if (thread.isLoading && !prevLoadingRef.current) {
      setAgentComputerOpen(true);
    }
    prevLoadingRef.current = thread.isLoading;
  }, [thread.isLoading, setAgentComputerOpen]);

  useEffect(() => {
    if (
      !isNewThread &&
      !isMock &&
      threadMetadata.data === null &&
      !threadMetadata.isLoading &&
      !threadMetadata.isFetching &&
      !isHistoryLoading &&
      !hasMoreHistory &&
      !hasThreadMessages
    ) {
      router.replace("/workspace/chats/new");
    }
  }, [
    hasMoreHistory,
    hasThreadMessages,
    isHistoryLoading,
    isMock,
    isNewThread,
    router,
    threadMetadata.data,
    threadMetadata.isFetching,
    threadMetadata.isLoading,
  ]);

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      const sendPromise = sendMessage(threadId, message);
      if (message.files.length > 0) {
        return sendPromise;
      }
      void sendPromise;
    },
    [sendMessage, threadId],
  );
  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);

  const handleAgentMessage = useCallback(
    (text: string) => {
      void sendMessage(threadId, { text, files: [] });
    },
    [sendMessage, threadId],
  );

  const tokenUsageInlineMode = tokenUsageEnabled
    ? localSettings.tokenUsage.inlineMode
    : "off";
  const hasTodos = (thread.values.todos?.length ?? 0) > 0;

  return (
    <ThreadContext.Provider
      value={{ thread, isMock, currentTool, taskProgress, verifyResult, llmError, activityEvents, activeWriteFilePath, onAgentMessage: handleAgentMessage }}
    >
      <ChatBox threadId={threadId}>
        <div className="relative flex size-full min-h-0 justify-between">
          <header
            className={cn(
              "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center gap-2 px-2 sm:px-4",
              isWelcomeMode
                ? "bg-background/0 backdrop-blur-none"
                : "bg-background/80 shadow-xs backdrop-blur",
            )}
          >
            <SidebarTrigger className="md:hidden" />
            <div className="flex min-w-0 flex-1 items-center text-sm font-medium">
              <ThreadTitle threadId={threadId} thread={thread} />
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <TokenUsageIndicator
                threadId={isNewThread ? undefined : threadId}
                backendUsage={backendTokenUsage}
                enabled={tokenUsageEnabled}
                messages={thread.messages}
                pendingMessages={pendingUsageMessages}
                preferences={localSettings.tokenUsage}
                onPreferencesChange={(preferences) =>
                  setLocalSettings("tokenUsage", preferences)
                }
              />
              <ExportTrigger threadId={threadId} />
              <ArtifactTrigger />
              <Tooltip content={t.agentComputer.header}>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  onClick={() => setAgentComputerOpen(!agentComputerOpen)}
                  className={cn(
                    agentComputerOpen && "bg-accent text-accent-foreground",
                  )}
                >
                  <TerminalIcon className="h-4 w-4" />
                </Button>
              </Tooltip>
            </div>
          </header>
          <main className="flex min-h-0 max-w-full grow flex-col">
            <div className="flex min-h-0 flex-1 justify-center">
              <MessageList
                className={cn("size-full", !isWelcomeMode && "pt-10")}
                threadId={threadId}
                thread={thread}
                paddingBottom={MESSAGE_LIST_DEFAULT_PADDING_BOTTOM}
                hasMoreHistory={hasMoreHistory}
                loadMoreHistory={loadMoreHistory}
                isHistoryLoading={isHistoryLoading}
                tokenUsageInlineMode={tokenUsageInlineMode}
              />
            </div>
            <div
              className={cn(
                "right-0 bottom-0 left-0 z-30 flex justify-center px-3 sm:px-4",
                isWelcomeMode ? "absolute" : "relative shrink-0 pb-4",
              )}
            >
              <div
                className={cn(
                  "relative w-full",
                  isWelcomeMode &&
                    "-translate-y-[calc(50vh-48px)] sm:-translate-y-[calc(50vh-96px)]",
                  isWelcomeMode
                    ? "max-w-(--container-width-sm)"
                    : "max-w-(--container-width-md)",
                )}
              >
                {hasTodos && (
                  <div
                    className={cn(
                      "right-0 left-0 z-0",
                      isWelcomeMode ? "absolute -top-4" : "relative",
                    )}
                  >
                    <div
                      className={cn(
                        "right-0 bottom-0 left-0",
                        isWelcomeMode ? "absolute" : "relative",
                      )}
                    >
                      <TodoList
                        className="bg-background/5"
                        todos={thread.values.todos ?? []}
                        hidden={false}
                      />
                    </div>
                  </div>
                )}
                {mountedRef.current ? (
                  <InputBox
                    className={cn(
                      "bg-background/5 w-full",
                      isWelcomeMode && "-translate-y-2 sm:-translate-y-4",
                    )}
                    isWelcomeMode={isWelcomeMode}
                    threadId={threadId}
                    autoFocus={isWelcomeMode}
                    initialValue={composerInitialValue}
                    status={
                      thread.error
                        ? "error"
                        : thread.isLoading
                          ? "streaming"
                          : "ready"
                    }
                    context={settings.context}
                    extraHeader={
                      isWelcomeMode && <Welcome mode={settings.context.mode} />
                    }
                    disabled={
                      isMock ||
                      env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ||
                      isUploading
                    }
                    onContextChange={(context) =>
                      setSettings("context", context)
                    }
                    onSubmit={handleSubmit}
                    onStop={handleStop}
                  />
                ) : (
                  <div
                    aria-hidden="true"
                    className={cn(
                      "bg-background/5 h-32 w-full rounded-2xl",
                      isWelcomeMode && "-translate-y-2 sm:-translate-y-4",
                    )}
                  />
                )}
                {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" && (
                  <div className="text-muted-foreground/67 w-full translate-y-12 text-center text-xs">
                    {t.common.notAvailableInDemoMode}
                  </div>
                )}
              </div>
            </div>
          </main>
        </div>
      </ChatBox>
    </ThreadContext.Provider>
  );
}
