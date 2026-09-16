"use client";

import { useQueryClient } from "@tanstack/react-query";
import { FilesIcon, LaptopIcon, MessageSquareIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GroupImperativeHandle } from "react-resizable-panels";
import { toast } from "sonner";

import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { AgentComputerPanel } from "@/components/workspace/agent-computer/agent-computer-panel";
import { WorkspaceStateProvider } from "@/components/workspace/agent-computer/workspace-state";
import { usePanels } from "@/components/workspace/panels/context";
import { RuntimeCapabilitiesBar } from "@/components/workspace/runtime-capabilities-bar";
import { useI18n } from "@/core/i18n/hooks";
import { foldCommandCount } from "@/core/sandbox/command-count";
import { useSandboxTerminalStats } from "@/core/sandbox/hooks";
import {
  useSupersedeStaleSubtasks,
  useUpdateSubtask,
} from "@/core/tasks/context";
import { useComputerEvents } from "@/core/threads/task-events-ws";
import { env } from "@/env";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";

import {
  ArtifactFileDetail,
  ArtifactFileList,
  useArtifacts,
} from "../artifacts";
import { useThread } from "../messages/context";

const CLOSE_MODE = { chat: 100, artifacts: 0 };
const OPEN_MODE = { chat: 60, artifacts: 40 };

/**
 * Below the mobile breakpoint there is no room to show chat, artifacts and the
 * Agent's Computer side by side (the computer panel alone has a 380px floor),
 * so the three become one full-width surface at a time behind this switcher.
 */
type MobilePanel = "chat" | "artifacts" | "computer";

function MobileTabBtn({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active}
      className={cn(
        "flex min-h-11 flex-1 items-center justify-center gap-1.5 text-xs font-medium transition-colors",
        active
          ? "text-foreground border-foreground/60 border-b-2"
          : "text-muted-foreground/70 border-b-2 border-transparent",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

const ChatBox: React.FC<{
  children: React.ReactNode;
  threadId: string;
  isNewThread?: boolean;
}> = ({ children, threadId, isNewThread = false }) => {
  const { t } = useI18n();
  const {
    thread,
    currentTool,
    activityEvents,
    activeWriteFilePath,
    onAgentMessage,
  } = useThread();
  const threadIdRef = useRef(threadId);
  const layoutRef = useRef<GroupImperativeHandle>(null);

  // Subagent task events over the thread-scoped WebSocket (WS-G). Additive to
  // the SSE custom stream: the subtask FSM treats duplicate application as a
  // no-op, and this path survives run end / reconnects, which is what lets a
  // todo binding arrive even if the SSE window missed it.
  const updateSubtaskForWs = useUpdateSubtask();
  const supersedeStaleSubtasks = useSupersedeStaleSubtasks();
  // Live tool_call ids of the CURRENT message stream — the membership test
  // that makes supersede deterministic instead of timing-based.
  const liveToolCallIds = useMemo(() => {
    const ids = new Set<string>();
    for (const m of thread.messages) {
      for (const tc of (m as { tool_calls?: Array<{ id?: string }> })
        .tool_calls ?? []) {
        if (tc.id) ids.add(tc.id);
      }
    }
    return ids;
  }, [thread.messages]);
  useEffect(() => {
    supersedeStaleSubtasks(liveToolCallIds);
  }, [liveToolCallIds, supersedeStaleSubtasks]);
  const queryClient = useQueryClient();
  useComputerEvents(threadId, {
    updateSubtask: updateSubtaskForWs,
    // The panel's live socket carries subagent status, todo bindings, dev-server
    // pushes and terminal counts. When the session dies it cannot reconnect, and
    // the REST/SSE legs keep rendering just enough that the panel looks alive —
    // so without this the whole live half goes missing with no signal at all.
    onAuthExpired: () => {
      toast.error(t.workspace.sessionExpiredTitle, {
        description: t.workspace.sessionExpiredDescription,
        duration: Infinity,
        action: {
          label: t.workspace.sessionExpiredAction,
          onClick: () => window.location.reload(),
        },
      });
    },
    // Dev-server transitions arrive as push: refresh the caches the panel's
    // Browser tab reads instead of waiting for the next poll interval.
    onDevServer: () => {
      void queryClient.invalidateQueries({
        queryKey: ["sandbox", "dev-status", threadId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["sandbox", "dev-servers", threadId],
      });
    },
    // A workspace observation means files changed under the panel. Invalidate
    // the file-content caches so the static preview refetches immediately —
    // this is what killed the stale `file://`-era snapshot class: the panel
    // kept showing an early render until its poll happened to catch up.
    onObservation: (event) => {
      const tool = event.tool;
      if (tool === "write_file" || tool === "str_replace" || tool === "bash") {
        void queryClient.invalidateQueries({
          queryKey: ["sandbox", "live-file", threadId],
        });
        void queryClient.invalidateQueries({
          queryKey: ["sandbox", "file", threadId],
        });
      }
      if (tool === "browser_check") {
        void queryClient.invalidateQueries({
          queryKey: ["sandbox", "browser-check-last", threadId],
        });
      }
      if (
        tool === "dev_verify" ||
        tool === "present_files" ||
        tool === "code_review"
      ) {
        void queryClient.invalidateQueries({
          queryKey: ["sandbox", "review", threadId],
        });
      }
    },
    // Authoritative todo snapshot: the checklist stops depending on when the
    // values stream happens to deliver state.
    onTodos: (snap) => {
      queryClient.setQueryData(["sandbox", "todo", threadId], snap.todos);
    },
    // Deterministic command counter for the Terminal header. State, not just
    // query cache: getQueryData is not reactive, so the header never
    // re-rendered on arrival.
    onTerminalStats: (stat) => {
      // Key must match `useSandboxTerminalStats` exactly, or this writes to a
      // cache entry nothing reads -- it was missing the "sandbox" segment, so
      // the socket never actually refreshed the seed it was trying to update.
      queryClient.setQueryData(
        ["sandbox", "terminal-stats", threadId],
        stat.total_commands,
      );
      // The hub replays its buffer to every late joiner, so this fires with
      // historical frames on each reconnect. Folding monotonically keeps a
      // replayed 146 from rewinding a live 313 and flickering the header.
      setTerminalCommandCount((current) =>
        foldCommandCount(current, stat.total_commands),
      );
    },
  });

  const {
    artifacts,
    open: artifactsOpen,
    setOpen: setArtifactsOpen,
    setArtifacts,
    select: selectArtifact,
    deselect,
    selectedArtifact,
  } = useArtifacts();

  const { agentComputerOpen, setAgentComputerOpen } = usePanels();

  // Resizable Agent's Computer width (drag the handle, or scroll-wheel over it).
  const [computerWidth, setComputerWidth] = useState<number>(() => {
    if (typeof window === "undefined") return 640;
    const saved = Number(localStorage.getItem("agent-computer-width"));
    return saved >= 380 && saved <= 1100 ? saved : 640;
  });
  const computerWidthRef = useRef(computerWidth);
  computerWidthRef.current = computerWidth;
  const clampWidth = (w: number) => Math.min(1100, Math.max(380, w));
  const startComputerResize = (e: React.MouseEvent) => {
    e.preventDefault();
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    const onMove = (ev: MouseEvent) =>
      setComputerWidth(clampWidth(window.innerWidth - ev.clientX));
    const onUp = () => {
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      localStorage.setItem(
        "agent-computer-width",
        String(Math.round(computerWidthRef.current)),
      );
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };
  const wheelComputerResize = (e: React.WheelEvent) => {
    setComputerWidth((w) => {
      const next = clampWidth(w - e.deltaY);
      localStorage.setItem("agent-computer-width", String(Math.round(next)));
      return next;
    });
  };

  const [autoSelectFirstArtifact, setAutoSelectFirstArtifact] = useState(true);
  useEffect(() => {
    if (threadIdRef.current !== threadId) {
      threadIdRef.current = threadId;
      deselect();
    }

    setArtifacts(thread.values.artifacts);

    if (
      env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" &&
      autoSelectFirstArtifact
    ) {
      if (thread?.values?.artifacts?.length > 0) {
        setAutoSelectFirstArtifact(false);
        selectArtifact(thread.values.artifacts[0]!);
      }
    }
  }, [
    threadId,
    autoSelectFirstArtifact,
    deselect,
    selectArtifact,
    selectedArtifact,
    setArtifacts,
    thread.values.artifacts,
  ]);

  const artifactPanelOpen = useMemo(() => {
    // Agent's Computer is the single preview surface — suppress the center
    // artifacts panel when it's open to avoid showing the same file twice.
    if (agentComputerOpen) return false;
    if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true") {
      return artifactsOpen && artifacts?.length > 0;
    }
    return artifactsOpen;
  }, [artifactsOpen, artifacts, agentComputerOpen]);

  // For unsaved threads the threadId is a client-generated uuid that differs
  // between the SSR snapshot and hydration (each render pass draws a fresh
  // uuid), which put a mismatched id/data-testid in the DOM and triggered a
  // React hydration error on every /chats/new load. Use the stable "chat-new"
  // id until the thread is persisted; layout prefs then also survive across
  // new chats.
  const resizableIdBase = useMemo(() => {
    if (isNewThread) {
      return "chat-new";
    }
    return `chat-${threadId}`
      .replace(/[^a-zA-Z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }, [isNewThread, threadId]);

  useEffect(() => {
    if (layoutRef.current) {
      if (artifactPanelOpen) {
        layoutRef.current.setLayout(OPEN_MODE);
      } else {
        layoutRef.current.setLayout(CLOSE_MODE);
      }
    }
  }, [artifactPanelOpen]);

  const isMobile = useIsMobile();
  const [mobilePanel, setMobilePanel] = useState<MobilePanel>("chat");

  // Deterministic command total pushed over computer-ws (drilled into the
  // Agent's Computer panel's Terminal header).
  const [terminalCommandCount, setTerminalCommandCount] = useState<
    number | undefined
  >(undefined);

  // Reset on thread change. This state lives in ChatBox, which is NOT remounted
  // when the thread changes -- only AgentComputerPanel is (key={threadId}). So
  // a fresh panel was handed the previous thread's total, and rendered it
  // *without* the "~" prefix, i.e. as an exact count, until thread B happened to
  // run its first command. `undefined` is the honest value: the Terminal header
  // falls back to "~N" from its local window, which reads as approximate.
  useEffect(() => {
    setTerminalCommandCount(undefined);
  }, [threadId]);

  // Seed from the durable counter on disk. The socket stays authoritative for
  // live updates -- this only fills the window where no `terminal_stats` frame
  // has arrived yet, which is the common case after a reload or once a burst of
  // commands has rolled the frame out of the hub's shared replay buffer.
  const seededCommandCount = useSandboxTerminalStats(threadId);
  useEffect(() => {
    setTerminalCommandCount((current) =>
      foldCommandCount(current, seededCommandCount),
    );
  }, [seededCommandCount]);

  // Opening either surface pulls it to the front, mirroring how they take over
  // screen space on desktop.
  useEffect(() => {
    if (agentComputerOpen) setMobilePanel("computer");
  }, [agentComputerOpen]);
  useEffect(() => {
    if (artifactPanelOpen) setMobilePanel("artifacts");
  }, [artifactPanelOpen]);

  // Derived rather than stored, so closing a panel can never strand the
  // switcher on a tab that is no longer rendered.
  const activeMobilePanel: MobilePanel =
    (mobilePanel === "computer" && !agentComputerOpen) ||
    (mobilePanel === "artifacts" && !artifactPanelOpen)
      ? "chat"
      : mobilePanel;
  const showMobileTabs = agentComputerOpen || artifactPanelOpen;

  const artifactsBody = selectedArtifact ? (
    <ArtifactFileDetail
      className="size-full"
      filepath={selectedArtifact}
      threadId={threadId}
    />
  ) : (
    <div className="relative flex size-full justify-center">
      <div className="absolute top-1 right-1 z-30">
        <Button
          size="icon-sm"
          variant="ghost"
          onClick={() => {
            setArtifactsOpen(false);
          }}
        >
          <XIcon />
        </Button>
      </div>
      {thread.values.artifacts?.length === 0 ? (
        <ConversationEmptyState
          icon={<FilesIcon />}
          title={t.a11y.noArtifact}
          description={t.a11y.artifacts}
        />
      ) : (
        <div className="flex size-full max-w-(--container-width-sm) flex-col justify-center p-4 pt-8">
          <header className="shrink-0">
            <h2 className="text-lg font-medium">{t.a11y.artifacts}</h2>
          </header>
          <main className="min-h-0 grow">
            <ArtifactFileList
              className="max-w-(--container-width-sm) p-4 pt-12"
              files={thread.values.artifacts ?? []}
              threadId={threadId}
            />
          </main>
        </div>
      )}
    </div>
  );

  // Status bar above the panel — purely additive. Fixed-height row; the panel
  // fills the remaining height below so its internal scroll areas and footer
  // are not clipped.
  const computerBody = (
    <>
      <RuntimeCapabilitiesBar
        className="shrink-0"
        sandboxEvents={activityEvents}
      />
      <div className="min-h-0 flex-1">
        <WorkspaceStateProvider
          threadId={threadId}
          todos={thread.values.todos ?? []}
        >
          {/* Keyed on threadId: resets browserFilePath/shownArtifactRef and
              other internal state when switching threads (the panel's tabs
              stay mounted-but-hidden across tab switches within one thread —
              see the "hidden" wrappers below — but a genuinely different
              thread should start the computer panel fresh, not inherit the
              previous thread's open file/artifact). */}
          <AgentComputerPanel
            key={threadId}
            threadId={threadId}
            currentTool={currentTool}
            isLoading={thread.isLoading}
            messages={thread.messages}
            activeWriteFilePath={activeWriteFilePath}
            terminalCommandCount={terminalCommandCount}
            artifacts={thread.values.artifacts ?? []}
            onClose={() => setAgentComputerOpen(false)}
            onAgentMessage={onAgentMessage}
          />
        </WorkspaceStateProvider>
      </div>
    </>
  );

  if (isMobile) {
    return (
      <div className="flex h-full w-full flex-col overflow-hidden">
        {showMobileTabs && (
          <nav
            aria-label={t.a11y.panels}
            className="border-border/60 flex shrink-0 border-b"
          >
            <MobileTabBtn
              active={activeMobilePanel === "chat"}
              onClick={() => setMobilePanel("chat")}
              icon={<MessageSquareIcon className="size-3.5" />}
              label={t.a11y.chat}
            />
            {artifactPanelOpen && (
              <MobileTabBtn
                active={activeMobilePanel === "artifacts"}
                onClick={() => setMobilePanel("artifacts")}
                icon={<FilesIcon className="size-3.5" />}
                label={t.a11y.artifacts}
              />
            )}
            {agentComputerOpen && (
              <MobileTabBtn
                active={activeMobilePanel === "computer"}
                onClick={() => setMobilePanel("computer")}
                icon={<LaptopIcon className="size-3.5" />}
                label={t.agentComputer.header}
              />
            )}
          </nav>
        )}

        {/* All surfaces stay mounted and are toggled with `hidden` so chat
            scroll position and the computer's tab state survive switching. */}
        <div className="min-h-0 flex-1">
          <div
            className={cn(
              "relative h-full",
              activeMobilePanel !== "chat" && "hidden",
            )}
          >
            {children}
          </div>
          {artifactPanelOpen && (
            <div
              className={cn(
                "h-full p-4",
                activeMobilePanel !== "artifacts" && "hidden",
              )}
            >
              {artifactsBody}
            </div>
          )}
          {/* Stays mounted while the thread lives (only `hidden`) so the
              computer's shell/tab state survives switching to chat and back;
              it is torn down only when the panel is closed, same trade-off
              as desktop but full-width surfaces make closed == gone. */}
          {agentComputerOpen && (
            <div
              className={cn(
                "flex h-full flex-col overflow-hidden",
                activeMobilePanel !== "computer" && "hidden",
              )}
            >
              {computerBody}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full overflow-hidden">
      {/* ── Centre: existing chat + artifacts split (unchanged) ── */}
      <div className="min-w-0 flex-1">
        <ResizablePanelGroup
          id={`${resizableIdBase}-panels`}
          orientation="horizontal"
          defaultLayout={{ chat: 100, artifacts: 0 }}
          groupRef={layoutRef}
        >
          <ResizablePanel className="relative" defaultSize={100} id="chat">
            {children}
          </ResizablePanel>
          <ResizableHandle
            id={`${resizableIdBase}-separator`}
            className={cn(
              "opacity-33 hover:opacity-100",
              !artifactPanelOpen && "pointer-events-none opacity-0",
            )}
          />
          <ResizablePanel
            className={cn(
              "transition-all duration-300 ease-in-out",
              !artifactsOpen && "opacity-0",
            )}
            id="artifacts"
          >
            <div
              className={cn(
                "h-full p-4 transition-transform duration-300 ease-in-out",
                artifactPanelOpen ? "translate-x-0" : "translate-x-full",
              )}
            >
              {artifactsBody}
            </div>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>

      {/* ── Right: Agent's Computer panel (resizable IDE surface) ──
          Stays MOUNTED across open/close: closing collapses the column to
          zero width (animated, so chat reflows continuously instead of
          snapping when the exit animation finished), but the panel's state —
          the interactive ttyd shell, browser nav history, scrollback, last
          tab — survives the toggle. Full teardown happens on thread switch
          via the panel's key. `invisible` keeps focus out of the collapsed
          surface. */}
      <div
        aria-hidden={!agentComputerOpen}
        className={cn(
          "flex h-full shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out",
          !agentComputerOpen && "invisible",
        )}
        style={{ width: agentComputerOpen ? computerWidth : 0 }}
      >
        {/* Drag (or scroll-wheel) this handle to resize the panel. */}
        <div
          onMouseDown={startComputerResize}
          onWheel={wheelComputerResize}
          title={t.a11y.dragResize}
          aria-label={t.a11y.dragResize}
          role="separator"
          className={cn(
            "bg-border/40 w-1 shrink-0 cursor-col-resize transition-colors hover:bg-[--primary]/60",
            !agentComputerOpen && "pointer-events-none",
          )}
        />
        <div
          style={{ width: computerWidth }}
          className="flex h-full shrink-0 flex-col overflow-hidden"
        >
          {computerBody}
        </div>
      </div>
    </div>
  );
};

export { ChatBox };
