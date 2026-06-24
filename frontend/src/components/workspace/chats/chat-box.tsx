import { FilesIcon, XIcon } from "lucide-react";
import { AnimatePresence } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GroupImperativeHandle } from "react-resizable-panels";

import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { AgentComputerPanel } from "@/components/workspace/agent-computer/agent-computer-panel";
import { usePanels } from "@/components/workspace/panels/context";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import {
  ArtifactFileDetail,
  ArtifactFileList,
  useArtifacts,
} from "../artifacts";
import { useThread } from "../messages/context";

const CLOSE_MODE = { chat: 100, artifacts: 0 };
const OPEN_MODE = { chat: 60, artifacts: 40 };

const ChatBox: React.FC<{ children: React.ReactNode; threadId: string }> = ({
  children,
  threadId,
}) => {
  const { thread, currentTool, taskProgress, activityEvents, activeWriteFilePath, onAgentMessage } = useThread();
  const threadIdRef = useRef(threadId);
  const layoutRef = useRef<GroupImperativeHandle>(null);

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
    const onMove = (ev: MouseEvent) => setComputerWidth(clampWidth(window.innerWidth - ev.clientX));
    const onUp = () => {
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      localStorage.setItem("agent-computer-width", String(Math.round(computerWidthRef.current)));
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

  // Derive the resizable group id from threadId (stable across SSR/client) rather
  // than pathname, which differs between the SSR snapshot and client navigation
  // and caused a hydration mismatch on the panel id/data-testid.
  const resizableIdBase = useMemo(() => {
    return `chat-${threadId}`.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  }, [threadId]);

  useEffect(() => {
    if (layoutRef.current) {
      if (artifactPanelOpen) {
        layoutRef.current.setLayout(OPEN_MODE);
      } else {
        layoutRef.current.setLayout(CLOSE_MODE);
      }
    }
  }, [artifactPanelOpen]);

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
              {selectedArtifact ? (
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
                      title="No artifact selected"
                      description="Select an artifact to view its details"
                    />
                  ) : (
                    <div className="flex size-full max-w-(--container-width-sm) flex-col justify-center p-4 pt-8">
                      <header className="shrink-0">
                        <h2 className="text-lg font-medium">Artifacts</h2>
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
              )}
            </div>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>

      {/* ── Right: Agent's Computer panel (resizable IDE surface) ── */}
      <AnimatePresence>
        {agentComputerOpen && (
          <div className="flex h-full shrink-0">
            {/* Drag (or scroll-wheel) this handle to resize the panel. */}
            <div
              onMouseDown={startComputerResize}
              onWheel={wheelComputerResize}
              title="Drag or scroll to resize"
              className="w-1 shrink-0 cursor-col-resize bg-border/40 transition-colors hover:bg-[--primary]/60"
            />
            <div style={{ width: computerWidth }} className="h-full shrink-0">
              <AgentComputerPanel
                threadId={threadId}
                currentTool={currentTool}
                isLoading={thread.isLoading}
                todos={thread.values.todos ?? []}
                taskProgress={taskProgress}
                messages={thread.messages}
                activityEvents={activityEvents}
                activeWriteFilePath={activeWriteFilePath}
                artifacts={thread.values.artifacts ?? []}
                onClose={() => setAgentComputerOpen(false)}
                onAgentMessage={onAgentMessage}
              />
            </div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
};

export { ChatBox };
