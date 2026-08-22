"use client";

import type { Message } from "@langchain/langgraph-sdk";
import {
  CheckCircle2Icon,
  DownloadIcon,
  FileTextIcon,
  FolderIcon,
  FolderOpenIcon,
  GithubIcon,
  GlobeIcon,
  PencilIcon,
  SquareTerminalIcon,
  ShieldIcon,
  XIcon,
} from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { AuroraText } from "@/components/ui/aurora-text";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ShineBorder } from "@/components/ui/shine-border";
import { AgentComputerErrorBoundary } from "@/components/workspace/agent-computer/agent-computer-error-boundary";
import { Tooltip } from "@/components/workspace/tooltip";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import {
  useDevServers,
  useDevServerStatus,
  useLiveFileContent,
  useSandboxFiles,
  useSandboxReview,
  useStartPreview,
  type SandboxFile,
} from "@/core/sandbox/hooks";
import { isTerminalTool } from "@/core/threads/tool-surface";
import { cn } from "@/lib/utils";

import { ActivityPanel, LlmErrorBadge, TaskChecklist } from "./activity-tab";
import { Browser } from "./browser-tab";
import { Editor } from "./editor-tab";
import { FilesPanel } from "./files-tab";
import { getActiveEdit, getActiveFilePath } from "./message-helpers";
import { PrivacyPanel } from "./privacy-tab";
import { ReviewPanel } from "./review-tab";
import { SkillLauncher } from "./skill-launcher";
import { StatusLine } from "./status-line";
import { Terminal } from "./terminal-tab";
import { useWorkspaceState } from "./workspace-state";

type PanelTab =
  | "files"
  | "terminal"
  | "editor"
  | "browser"
  | "activity"
  | "review"
  | "privacy";

function TabBtn({
  active,
  onClick,
  children,
  badge,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  badge?: number;
}) {
  return (
    <button
      onClick={onClick}
      role="tab"
      aria-selected={active}
      className={cn(
        "relative flex shrink-0 items-center gap-1 px-2.5 py-3 text-[11px] font-medium transition-colors md:py-1.5",
        active
          ? "text-foreground"
          : "text-muted-foreground/60 hover:text-muted-foreground",
      )}
    >
      {children}
      {badge !== undefined && badge > 0 && (
        <motion.span
          key={badge}
          initial={{ scale: 0.6, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", stiffness: 500, damping: 22 }}
          className="bg-muted text-muted-foreground rounded-full px-1 text-[9px] leading-none"
        >
          {badge}
        </motion.span>
      )}
      {active && (
        <motion.div
          layoutId="agent-computer-tab-underline"
          className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-gradient-to-r from-violet-500 to-cyan-400"
          transition={{ type: "spring", stiffness: 400, damping: 32 }}
        />
      )}
    </button>
  );
}

// ──────────────────────────────────────────────────────────
// Recon Panel — search, fetch and audit observability
// ──────────────────────────────────────────────────────────

// ──────────────────────────────────────────────────────────
export interface AgentComputerPanelProps {
  threadId: string;
  currentTool: string | null;
  isLoading: boolean;
  messages: Message[];
  activeWriteFilePath: string | null;
  artifacts?: string[];
  onClose: () => void;
  onAgentMessage?: (text: string) => void;
}

export function AgentComputerPanel({
  threadId,
  currentTool,
  isLoading,
  messages,
  activeWriteFilePath,
  artifacts = [],
  onClose,
  onAgentMessage,
}: AgentComputerPanelProps) {
  const { t } = useI18n();
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mq.matches);
    const listener = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mq.addEventListener("change", listener);
    return () => mq.removeEventListener("change", listener);
  }, []);
  // Workspace state (C10 Batch 0): activity events, todos, task progress,
  // verification, and the merged sandbox.log timeline come from the
  // WorkspaceStateProvider mounted in chat-box.tsx.
  const {
    activityEvents,
    mergedEvents,
    todos,
    taskProgress,
    verifyResult,
    llmError,
  } = useWorkspaceState();
  const effectiveVerifyResult = verifyResult;
  const effectiveLlmError = llmError;
  const files = useSandboxFiles(threadId);
  const startPreview = useStartPreview(threadId);
  const hasRunnableProject = files.some((f) => f.name === "package.json");
  const devServers = useDevServers(threadId);
  const [selectedLabel, setSelectedLabel] = useState("app");
  // Fall back to an existing label if the selected one is gone (e.g. stopped).
  const activeLabel = devServers.some((s) => s.label === selectedLabel)
    ? selectedLabel
    : (devServers[0]?.label ?? "app");
  const devServer = useDevServerStatus(threadId, activeLabel);
  const msgFilePath = getActiveFilePath(messages);
  const derivedFilePath = activeWriteFilePath ?? msgFilePath;
  const activeEdit = useMemo(() => getActiveEdit(messages), [messages]);

  // Session-storage persisted preview path
  const storageKey = `agent-computer-preview:${threadId}`;
  const [browserFilePath, setBrowserFilePath] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return sessionStorage.getItem(storageKey) ?? null;
  });

  // Update browser path when agent writes
  useEffect(() => {
    if (derivedFilePath) {
      setBrowserFilePath(derivedFilePath);
      sessionStorage.setItem(storageKey, derivedFilePath);
    }
  }, [derivedFilePath, storageKey]);

  const [activeTab, setActiveTab] = useState<PanelTab>(() => {
    if (typeof window !== "undefined" && sessionStorage.getItem(storageKey))
      return "browser";
    return "activity";
  });
  const reviewQuery = useSandboxReview(threadId, activeTab === "review");

  // Auto-switch tabs based on current tool
  useEffect(() => {
    if (!currentTool) return;
    if (
      currentTool === "write_file" ||
      currentTool === "str_replace" ||
      currentTool === "scaffold_project"
    ) {
      setActiveTab("editor");
    } else if (
      currentTool === "bash" ||
      currentTool === "execute_command" ||
      currentTool === "search_files" ||
      currentTool === "grep_files"
    ) {
      setActiveTab("terminal");
    } else if (currentTool === "start_dev_server") {
      setActiveTab("browser");
    }
  }, [currentTool]);

  // Auto-switch to Browser when the dev server comes up
  const prevDevRunning = useRef(false);
  useEffect(() => {
    if (devServer.running && !prevDevRunning.current) {
      setActiveTab("browser");
    }
    prevDevRunning.current = devServer.running;
  }, [devServer.running]);

  // Switch to browser after first write completes
  const firstWriteDone = mergedEvents.some((e) => e.type === "write_file");
  const prevFirstWriteDone = useRef(false);
  useEffect(() => {
    if (firstWriteDone && !prevFirstWriteDone.current) {
      setActiveTab("browser");
    }
    prevFirstWriteDone.current = firstWriteDone;
  }, [firstWriteDone]);

  // Canonical preview for self-contained deliverables: when the agent ships an
  // HTML file via present_files (→ artifacts) and no live dev server is up, show
  // THAT file in the Browser tab. Fixes "preview not there" when a static HTML
  // was delivered (no server) or a stale/dead dev server was lingering.
  const latestHtmlArtifact = useMemo(
    () => [...artifacts].reverse().find((a) => /\.html?$/i.test(a)) ?? null,
    [artifacts],
  );
  const shownArtifactRef = useRef<string | null>(null);
  useEffect(() => {
    if (
      latestHtmlArtifact &&
      latestHtmlArtifact !== shownArtifactRef.current &&
      !devServer.running
    ) {
      shownArtifactRef.current = latestHtmlArtifact;
      setBrowserFilePath(latestHtmlArtifact);
      sessionStorage.setItem(storageKey, latestHtmlArtifact);
      setActiveTab("browser");
    }
  }, [latestHtmlArtifact, devServer.running, storageKey]);

  const handleSelectFile = useCallback(
    (file: SandboxFile) => {
      setBrowserFilePath(file.virtual_path);
      sessionStorage.setItem(storageKey, file.virtual_path);
      setActiveTab("browser");
    },
    [storageKey],
  );

  const handleSelectArtifact = useCallback(
    (path: string) => {
      setBrowserFilePath(path);
      sessionStorage.setItem(storageKey, path);
      setActiveTab("browser");
    },
    [storageKey],
  );

  // Live line count for status
  const { lineCount } = useLiveFileContent(
    threadId,
    derivedFilePath,
    (currentTool === "write_file" || currentTool === "str_replace") &&
      Boolean(derivedFilePath),
  );

  // Download button
  const handleDownload = useCallback(async () => {
    const target = browserFilePath ?? files[0]?.virtual_path;
    if (!target) return;
    try {
      const res = await window.fetch(
        `${getBackendBaseURL()}/api/sandbox/file?thread_id=${encodeURIComponent(threadId)}&path=${encodeURIComponent(target)}`,
        { credentials: "include" },
      );
      const data = (await res.json()) as { content: string; exists: boolean };
      if (!res.ok || !data.exists || !data.content) {
        toast.error(t.agentComputer.downloadFailed);
        return;
      }
      const filename = target.split("/").at(-1) ?? "file";
      const blob = new Blob([data.content], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error(t.agentComputer.downloadFailed);
    }
  }, [threadId, browserFilePath, files, t.agentComputer.downloadFailed]);

  // Download ALL workspace files as zip
  const handleDownloadZip = useCallback(() => {
    const url = `${getBackendBaseURL()}/api/sandbox/download-zip?thread_id=${encodeURIComponent(threadId)}`;
    const a = document.createElement("a");
    a.href = url;
    a.download = `workspace-${threadId.slice(0, 8)}.zip`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, [threadId]);

  // GitHub push
  const handleGitHubPush = useCallback(() => {
    if (!onAgentMessage) return;
    const repoName = `nova-${Date.now()}`;
    // Use http.extraHeader for auth — avoids embedding GITHUB_TOKEN in the remote
    // URL (which would leak it into .git/config, shell history, and process lists).
    onAgentMessage(
      `Push all files in /mnt/user-data/workspace/ to a new public GitHub repository named "${repoName}". ` +
        `Steps: cd /mnt/user-data/workspace && git init && git add -A && ` +
        `git commit -m "Built by Nova" && ` +
        `git remote add origin https://github.com/$(git config user.name || echo "user")/${repoName}.git && ` +
        `git -c http.extraHeader="Authorization: Bearer $GITHUB_TOKEN" push -u origin main. ` +
        `Report the GitHub URL when done. If GITHUB_TOKEN is not set, ask the user to set it in the sandbox environment.`,
    );
    setActiveTab("terminal");
  }, [onAgentMessage]);

  const terminalCount = mergedEvents.filter((e) =>
    isTerminalTool(e.type),
  ).length;

  return (
    <motion.div
      initial={{ x: 320, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 320, opacity: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className="border-border/50 bg-card/50 flex h-full w-full flex-col overflow-hidden border-l backdrop-blur-sm"
    >
      {/* ── Header ── */}
      <div className="border-border/50 bg-card/50 relative flex h-10 shrink-0 items-center justify-between overflow-hidden border-b px-3 backdrop-blur-sm">
        {!reducedMotion && (isLoading || currentTool) && (
          <ShineBorder
            borderWidth={1}
            duration={8}
            shineColor={["#8b5cf6", "#06b6d4", "#a78bfa", "#8b5cf6"]}
          />
        )}
        {/* Left: live status pill (replaces the redundant title — page-header toggle owns the name) */}
        <div className="relative z-10 flex min-w-0 items-center">
          {isLoading && (
            <span className="bg-primary/10 inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium">
              <span className="relative inline-flex h-1.5 w-1.5 shrink-0">
                <span className="bg-primary absolute inline-flex h-full w-full animate-ping rounded-full opacity-50" />
                <span className="bg-primary relative inline-flex h-1.5 w-1.5 rounded-full" />
              </span>
              {reducedMotion ? (
                <span className="text-[10px] font-medium">
                  {t.agentComputer.live}
                </span>
              ) : (
                <AuroraText
                  colors={["#8b5cf6", "#a78bfa", "#06b6d4", "#8b5cf6"]}
                  speed={1.5}
                  className="text-[10px] font-medium"
                >
                  {t.agentComputer.live}
                </AuroraText>
              )}
            </span>
          )}
        </div>
        <div className="relative z-10 flex items-center gap-0.5">
          {/* Primary: run a skill */}
          {onAgentMessage && (
            <SkillLauncher
              onRun={(text) => {
                onAgentMessage(text);
                setActiveTab("terminal");
              }}
            />
          )}
          {/* Download all (zip) — visible primary action */}
          {files.length > 0 && (
            <Tooltip content={t.agentComputer.downloadAllZip}>
              <Button
                size="icon-sm"
                variant="ghost"
                className="h-9 w-9 md:h-6 md:w-6"
                onClick={handleDownloadZip}
              >
                <FolderOpenIcon className="h-3.5 w-3.5" />
              </Button>
            </Tooltip>
          )}
          {/* Download active file — only when the editor has an open file */}
          {browserFilePath && (
            <Tooltip content={t.agentComputer.downloadActiveFile}>
              <Button
                size="icon-sm"
                variant="ghost"
                className="h-9 w-9 md:h-6 md:w-6"
                onClick={handleDownload}
              >
                <DownloadIcon className="h-3.5 w-3.5" />
              </Button>
            </Tooltip>
          )}
          {/* Push to GitHub */}
          {onAgentMessage && (
            <Tooltip content={t.agentComputer.pushToGithub}>
              <Button
                size="icon-sm"
                variant="ghost"
                className="h-9 w-9 md:h-6 md:w-6"
                onClick={handleGitHubPush}
              >
                <GithubIcon className="h-3.5 w-3.5" />
              </Button>
            </Tooltip>
          )}
          <Tooltip content={t.common.close}>
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={onClose}
              // The tooltip is the only label on hover-capable devices; touch
              // never fires :hover, so the button also needs a real name.
              // Names the panel too — a bare "Close" collides with the
              // artifacts panel's own close button.
              aria-label={`${t.common.close} ${t.agentComputer.header}`}
              className="h-9 w-9 md:h-6 md:w-6"
            >
              <XIcon className="h-3.5 w-3.5" />
            </Button>
          </Tooltip>
        </div>
      </div>

      {/* ── Status line ── */}
      <StatusLine
        tool={currentTool}
        isLoading={isLoading}
        filePath={derivedFilePath}
        lineCount={
          currentTool === "write_file" && lineCount > 1 ? lineCount : undefined
        }
      />

      {/* ── Tab bar ── */}
      <div
        role="tablist"
        aria-label={t.agentComputer.header}
        className="border-border/50 scrollbar-none flex shrink-0 overflow-x-auto border-b"
      >
        <TabBtn
          active={activeTab === "files"}
          onClick={() => setActiveTab("files")}
        >
          <FolderIcon className="h-3 w-3" />
          {t.agentComputer.tabs.files}
          {files.length > 0 && (
            <span className="bg-muted text-muted-foreground rounded-full px-1 text-[9px]">
              {files.length}
            </span>
          )}
        </TabBtn>
        <TabBtn
          active={activeTab === "terminal"}
          onClick={() => setActiveTab("terminal")}
        >
          <SquareTerminalIcon className="h-3 w-3" />
          {t.agentComputer.tabs.terminal}
          {terminalCount > 0 && (
            <span className="rounded-full bg-emerald-500/20 px-1 text-[9px] text-emerald-400">
              {terminalCount}
            </span>
          )}
        </TabBtn>
        <TabBtn
          active={activeTab === "editor"}
          onClick={() => setActiveTab("editor")}
        >
          <PencilIcon className="h-3 w-3" />
          {t.agentComputer.tabs.editor}
        </TabBtn>
        <TabBtn
          active={activeTab === "browser"}
          onClick={() => setActiveTab("browser")}
        >
          <GlobeIcon className="h-3 w-3" />
          {t.agentComputer.tabs.browser}
        </TabBtn>
        <TabBtn
          active={activeTab === "activity"}
          onClick={() => setActiveTab("activity")}
        >
          <FileTextIcon className="h-3 w-3" />
          {t.agentComputer.tabs.activity}
        </TabBtn>
        <TabBtn
          active={activeTab === "review"}
          onClick={() => setActiveTab("review")}
        >
          <CheckCircle2Icon className="h-3 w-3" />
          {t.agentComputer.tabs.review}
        </TabBtn>
        <TabBtn
          active={activeTab === "privacy"}
          onClick={() => setActiveTab("privacy")}
        >
          <ShieldIcon className="h-3 w-3" />
          {t.agentComputer.tabs.privacy}
        </TabBtn>
      </div>

      {effectiveLlmError ? <LlmErrorBadge event={effectiveLlmError} /> : null}

      {/* ── Tab content ──
          All tabs stay mounted and are toggled with `hidden` (matching the
          mobile chat/artifacts/computer switcher's pattern in chat-box.tsx)
          so scroll position, in-progress edits, terminal mode, and browser
          nav history survive switching tabs — the previous ternary-unmount
          approach reset all of that on every tab hop. Components that fetch
          their own data (Editor, Browser, Activity, Privacy) already accept
          an active/enabled-style prop to gate their queries while hidden. */}
      <div className="min-h-0 flex-1 overflow-hidden">
        <AgentComputerErrorBoundary tabName="Files">
          <ScrollArea
            data-tab="files"
            className={cn("h-full", activeTab !== "files" && "hidden")}
          >
            <FilesPanel
              files={files}
              artifacts={artifacts}
              onSelectFile={handleSelectFile}
              onSelectArtifact={handleSelectArtifact}
              threadId={threadId}
              runningEvents={mergedEvents}
              active={activeTab === "files"}
            />
          </ScrollArea>
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Terminal">
          <div
            data-tab="terminal"
            className={cn("h-full", activeTab !== "terminal" && "hidden")}
          >
            <Terminal
              events={mergedEvents}
              threadId={threadId}
              active={activeTab === "terminal"}
            />
          </div>
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Editor">
          <div
            data-tab="editor"
            className={cn("h-full", activeTab !== "editor" && "hidden")}
          >
            <Editor
              threadId={threadId}
              filePath={derivedFilePath}
              isWriting={
                currentTool === "write_file" || currentTool === "str_replace"
              }
              activeTab={activeTab === "editor"}
              activeEdit={activeEdit}
            />
          </div>
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Browser">
          <div
            data-tab="browser"
            className={cn("h-full", activeTab !== "browser" && "hidden")}
          >
            <Browser
              threadId={threadId}
              filePath={browserFilePath}
              devServer={devServer}
              devServers={devServers}
              selectedLabel={activeLabel}
              onSelectLabel={setSelectedLabel}
              onStartPreview={startPreview}
              hasRunnableProject={hasRunnableProject}
              onAgentMessage={onAgentMessage}
              entryHtmlArtifact={latestHtmlArtifact}
              active={activeTab === "browser"}
            />
          </div>
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Review">
          <div
            data-tab="review"
            className={cn("h-full", activeTab !== "review" && "hidden")}
          >
            <ReviewPanel
              threadId={threadId}
              review={reviewQuery.data ?? undefined}
              isFetching={reviewQuery.isFetching}
              onRegenerate={() => void reviewQuery.refetch()}
              active={activeTab === "review"}
            />
          </div>
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Activity">
          <div
            data-tab="activity"
            className={cn("h-full", activeTab !== "activity" && "hidden")}
          >
            <ActivityPanel
              events={mergedEvents}
              threadId={threadId}
              verifyResult={effectiveVerifyResult}
              active={activeTab === "activity"}
            />
          </div>
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Privacy">
          <div
            data-tab="privacy"
            className={cn("h-full", activeTab !== "privacy" && "hidden")}
          >
            <PrivacyPanel
              threadId={threadId}
              active={activeTab === "privacy"}
            />
          </div>
        </AgentComputerErrorBoundary>
      </div>

      {/* ── Task checklist ── */}
      {todos.length > 0 && (
        <div className="border-border/50 shrink-0 border-t">
          <TaskChecklist
            todos={todos}
            taskProgress={taskProgress}
            activityEvents={activityEvents}
          />
        </div>
      )}
    </motion.div>
  );
}
