"use client";

import type { AIMessage, Message } from "@langchain/langgraph-sdk";
import {
  BracesIcon,
  CheckCircle2Icon,
  CircleIcon,
  CodeIcon,
  DownloadIcon,
  ExternalLinkIcon,
  EyeIcon,
  FileIcon,
  FileSearchIcon,
  FileTextIcon,
  FolderIcon,
  FolderOpenIcon,
  GithubIcon,
  GlobeIcon,
  AlertTriangleIcon,
  LoaderCircleIcon,
  MonitorIcon,
  MoreHorizontalIcon,
  PaletteIcon,
  PencilIcon,
  SparklesIcon,
  SquareTerminalIcon,
  ShieldIcon,
  TerminalIcon,
  XIcon,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AuroraText } from "@/components/ui/aurora-text";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ShineBorder } from "@/components/ui/shine-border";
import { Switch } from "@/components/ui/switch";
import { AgentComputerErrorBoundary } from "@/components/workspace/agent-computer/agent-computer-error-boundary";
import type {
  LlmError,
  TaskProgress,
  VerifyResult,
} from "@/components/workspace/messages/context";
import { useThread } from "@/components/workspace/messages/context";
import { Tooltip } from "@/components/workspace/tooltip";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { useIGINOStatus, useToggleIGINO } from "@/core/igino/hooks";
import {
  sandboxAuditDownloadUrl,
  sandboxReviewDownloadUrl,
  useBrowserCheck,
  useLastBrowserCheck,
  useDevServers,
  useDevServerStatus,
  useLiveFileContent,
  useSandboxFile,
  useSandboxFiles,
  useSandboxReview,
  useSandboxTerminalUrl,
  useStartPreview,
  useSandboxLogs,
  type SandboxEvent,
  type SandboxFile,
  type SandboxReview,
} from "@/core/sandbox/hooks";
import { useSkills } from "@/core/skills/hooks";
import type { Skill } from "@/core/skills/type";
import type { AgentActivityEvent } from "@/core/threads/hooks";
import type { Todo } from "@/core/todos";
import { diffStats, lineDiff, type DiffLine } from "@/lib/line-diff";
import { cn } from "@/lib/utils";

// ──────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────

function getActiveFilePath(messages: Message[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (!msg || msg.type !== "ai") continue;
    const calls = (msg as AIMessage).tool_calls ?? [];
    for (let j = calls.length - 1; j >= 0; j--) {
      const call = calls[j];
      if (!call) continue;
      if (
        call.name === "write_file" ||
        call.name === "str_replace" ||
        call.name === "read_file"
      ) {
        const path = (call.args as Record<string, unknown>)?.path;
        if (typeof path === "string") return path;
      }
    }
  }
  return null;
}

// The most recent file edit, sourced from tool-call args already in state — used
// by the Editor's live-diff view (str_replace carries old/new; write_file is all-add).
type ActiveEdit =
  | { path: string; kind: "str_replace"; oldStr: string; newStr: string }
  | { path: string; kind: "write_file"; content: string };

function getActiveEdit(messages: Message[]): ActiveEdit | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (!msg || msg.type !== "ai") continue;
    const calls = (msg as AIMessage).tool_calls ?? [];
    for (let j = calls.length - 1; j >= 0; j--) {
      const call = calls[j];
      if (!call) continue;
      const args = (call.args ?? {}) as Record<string, unknown>;
      const path = typeof args.path === "string" ? args.path : null;
      if (!path) continue;
      if (
        call.name === "str_replace" &&
        typeof args.old_str === "string" &&
        typeof args.new_str === "string"
      ) {
        return {
          path,
          kind: "str_replace",
          oldStr: args.old_str,
          newStr: args.new_str,
        };
      }
      if (call.name === "write_file" && typeof args.content === "string") {
        return { path, kind: "write_file", content: args.content };
      }
    }
  }
  return null;
}

function getStatusLabel(
  tool: string | null,
  isLoading: boolean,
  filePath: string | null,
  lineCount: number | undefined,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const filename = filePath?.split("/").at(-1);
  if (tool === "write_file") {
    return filename
      ? t.agentComputer.status.writing(
          filename,
          lineCount && lineCount > 1 ? `${lineCount} lines` : undefined,
        )
      : t.agentComputer.status.usingEditor;
  }
  if (tool === "str_replace")
    return filename
      ? t.agentComputer.status.editing(filename)
      : t.agentComputer.status.usingEditor;
  if (tool === "read_file")
    return filename
      ? t.agentComputer.status.reading(filename)
      : t.agentComputer.status.usingEditor;
  if (tool === "bash" || tool === "execute_command")
    return t.agentComputer.status.usingTerminal;
  if (tool === "search_files") return t.agentComputer.status.searchingFiles;
  if (tool === "grep_files") return t.agentComputer.status.searchingContent;
  if (tool === "task") return t.agentComputer.status.delegatingToSubagent;
  if (tool === "scaffold_project")
    return t.agentComputer.status.scaffoldingProject;
  if (tool === "browser" || tool === "web_search" || tool === "tavily_search")
    return t.agentComputer.status.usingBrowser;
  if (isLoading) return t.agentComputer.status.isThinking;
  return t.agentComputer.status.isIdle;
}

function getDotClass(tool: string | null, isLoading: boolean): string {
  if (tool === "bash" || tool === "execute_command") return "bg-emerald-400";
  if (tool === "write_file" || tool === "scaffold_project")
    return "bg-blue-400";
  if (tool === "str_replace") return "bg-purple-400";
  if (tool === "read_file") return "bg-sky-400";
  if (tool === "search_files" || tool === "grep_files") return "bg-orange-400";
  if (tool === "task") return "bg-indigo-400";
  if (isLoading) return "bg-yellow-400";
  return "bg-muted-foreground/40";
}

// ──────────────────────────────────────────────────────────
// Status line
// ──────────────────────────────────────────────────────────
function StatusLine({
  tool,
  isLoading,
  filePath,
  lineCount,
}: {
  tool: string | null;
  isLoading: boolean;
  filePath: string | null;
  lineCount?: number;
}) {
  const { t } = useI18n();
  const label = getStatusLabel(tool, isLoading, filePath, lineCount, t);
  const dotClass = getDotClass(tool, isLoading);
  const pulse = isLoading || Boolean(tool);
  const startRef = useRef<number | null>(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (pulse && startRef.current === null) {
      startRef.current = Date.now();
    } else if (!pulse) {
      startRef.current = null;
      setElapsed(0);
      return;
    }
    const id = setInterval(() => {
      if (startRef.current !== null) {
        setElapsed(Math.floor((Date.now() - startRef.current) / 1000));
      }
    }, 1000);
    return () => {
      clearInterval(id);
    };
  }, [pulse]);

  return (
    <div className="border-border/50 flex items-center gap-2 border-b px-3 py-1.5">
      <span className="relative inline-flex h-2 w-2 shrink-0">
        {pulse && (
          <span
            className={cn(
              "absolute inset-0 -m-1 rounded-full opacity-40 motion-safe:animate-ping",
              dotClass,
            )}
            aria-hidden="true"
          />
        )}
        <span
          className={cn(
            "relative inline-block h-2 w-2 shrink-0 rounded-full transition-colors duration-300",
            dotClass,
            pulse && "animate-pulse",
          )}
        />
      </span>
      <div className="relative min-w-0 flex-1 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.span
            key={label}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.12 }}
            className="text-muted-foreground flex items-center gap-1 truncate text-xs"
          >
            <span className="truncate">{label}</span>
            {elapsed > 0 && (
              <span className="shrink-0 text-[10px] text-muted-foreground/60">
                · {elapsed}s
              </span>
            )}
          </motion.span>
        </AnimatePresence>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Tab 1: Terminal — bash/search/grep events, always expanded
// ──────────────────────────────────────────────────────────
// Note: "task" (delegate to subagent) stays in Activity, not Terminal
const TERMINAL_TOOLS = new Set([
  "bash",
  "execute_command",
  "search_files",
  "grep_files",
]);

function Terminal({
  events,
  threadId,
}: {
  events: AgentActivityEvent[];
  threadId: string;
}) {
  const { t } = useI18n();
  const bottomRef = useRef<HTMLDivElement>(null);
  const terminalEvents = events.filter((e) => TERMINAL_TOOLS.has(e.type));
  // "shell" = the sandbox's real interactive ttyd terminal (type into it live);
  // "stream" = the agent's command output log.
  const [mode, setMode] = useState<"stream" | "shell">("stream");
  const { terminal: terminalUrl } = useSandboxTerminalUrl(
    threadId,
    mode === "shell",
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [terminalEvents.length, terminalEvents.at(-1)?.output]);

  const ModeToggle = (
    <div className="border-border/30 flex shrink-0 items-center gap-1 border-b bg-black/40 px-2 py-1">
      <span className="text-muted-foreground/50 mr-auto font-mono text-[10px]">
        {t.agentComputer.terminal.tab}
      </span>
      <div className="border-border/40 flex items-center rounded border text-[10px]">
        <button
          onClick={() => setMode("stream")}
          className={cn(
            "px-1.5 py-0.5",
            mode === "stream"
              ? "bg-muted text-foreground"
              : "text-muted-foreground/60 hover:text-muted-foreground",
          )}
        >
          {t.agentComputer.terminal.stream}
        </button>
        <button
          onClick={() => setMode("shell")}
          className={cn(
            "px-1.5 py-0.5",
            mode === "shell"
              ? "bg-muted text-foreground"
              : "text-muted-foreground/60 hover:text-muted-foreground",
          )}
        >
          {t.agentComputer.terminal.shell}
        </button>
      </div>
    </div>
  );

  if (mode === "shell") {
    return (
      <div className="flex h-full flex-col">
        {ModeToggle}
        <div className="min-h-0 flex-1 bg-black">
          {terminalUrl ? (
            <iframe
              key={terminalUrl}
              src={terminalUrl}
              title={t.agentComputer.terminal.interactiveTitle}
              className="h-full w-full border-0"
            />
          ) : (
            <div className="flex h-full items-center justify-center">
              <LoaderCircleIcon className="text-muted-foreground/30 h-5 w-5 animate-spin" />
            </div>
          )}
        </div>
      </div>
    );
  }

  if (terminalEvents.length === 0) {
    return (
      <div className="flex h-full flex-col">
        {ModeToggle}
        <div className="flex flex-1 flex-col items-center justify-center gap-3 bg-black/50 text-center">
          <div className="animate-pulse font-mono text-xl text-emerald-400/30">
            ▮
          </div>
          <div>
            <p className="text-muted-foreground/60 text-xs font-medium">
              {t.agentComputer.terminal.noOutput}
            </p>
            <p className="text-muted-foreground/40 mt-1 text-[10px]">
              {t.agentComputer.terminal.noOutputHint}{" "}
              <span className="text-emerald-400/70">
                {t.agentComputer.terminal.shell}
              </span>
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-black/70">
      {ModeToggle}
      <div className="min-h-0 flex-1 overflow-y-auto p-3 font-mono text-xs">
        {terminalEvents.map((event, i) => (
          <div key={i} className="mb-4">
            {/* Command prompt line */}
            <div className="mb-1.5 flex items-center gap-1.5">
              <span className="text-emerald-500/70 select-none">❯</span>
              <span
                className={cn(
                  "flex-1 font-medium",
                  event.type === "search_files" || event.type === "grep_files"
                    ? "text-orange-400"
                    : "text-emerald-400",
                )}
              >
                {event.summary}
              </span>
              {event.status === "running" && (
                <LoaderCircleIcon className="h-3 w-3 shrink-0 animate-spin text-emerald-400/50" />
              )}
              <span className="text-muted-foreground/30 shrink-0 text-[10px]">
                {event.ts}
              </span>
            </div>
            {/* Output — always shown, no click needed */}
            {event.output ? (
              <pre
                className={cn(
                  "border-l pl-4 leading-relaxed break-all whitespace-pre-wrap",
                  event.status === "error"
                    ? "border-red-900/50 text-red-400"
                    : "border-emerald-900/30 text-emerald-300/80",
                )}
              >
                {event.output}
              </pre>
            ) : event.status === "running" ? (
              <div className="pl-4 text-emerald-400/40">
                <span className="animate-pulse">
                  {t.agentComputer.terminal.running}
                </span>
              </div>
            ) : null}
          </div>
        ))}
        <div ref={bottomRef} />
        <span className="animate-pulse text-emerald-400/60">▮</span>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Tab 2: Editor — live code view as agent writes
// ──────────────────────────────────────────────────────────
function getCodeColor(filename: string): string {
  const ext = filename.split(".").at(-1)?.toLowerCase() ?? "";
  if (ext === "html" || ext === "htm") return "text-orange-300";
  if (ext === "css" || ext === "scss") return "text-pink-300";
  if (ext === "js" || ext === "jsx" || ext === "mjs") return "text-yellow-300";
  if (ext === "ts" || ext === "tsx") return "text-blue-300";
  if (ext === "json") return "text-green-300";
  if (ext === "md") return "text-sky-300";
  if (ext === "py") return "text-emerald-300";
  return "text-slate-300";
}

// Renders an interleaved red/green line diff (zai/cursor style).
function DiffView({ lines }: { lines: DiffLine[] }) {
  return (
    <pre className="min-w-full p-0 font-mono text-[11px] leading-relaxed">
      {lines.map((l, i) => (
        <div
          key={i}
          className={cn(
            "flex px-2 break-all whitespace-pre-wrap",
            l.type === "add" && "bg-emerald-500/10 text-emerald-300",
            l.type === "del" && "bg-red-500/10 text-red-300/90",
            l.type === "ctx" && "text-muted-foreground/70",
          )}
        >
          <span className="mr-2 inline-block w-3 shrink-0 text-center opacity-60 select-none">
            {l.type === "add" ? "+" : l.type === "del" ? "−" : " "}
          </span>
          <span className="min-w-0 flex-1">{l.text || " "}</span>
        </div>
      ))}
    </pre>
  );
}

function Editor({
  threadId,
  filePath,
  isWriting,
  activeTab,
  activeEdit,
}: {
  threadId: string;
  filePath: string | null;
  isWriting: boolean;
  activeTab: boolean;
  activeEdit: ActiveEdit | null;
}) {
  const { t } = useI18n();
  const { content, exists, lineCount } = useLiveFileContent(
    threadId,
    filePath,
    activeTab && Boolean(filePath),
  );
  const bottomRef = useRef<HTMLDivElement>(null);
  const filename = filePath?.split("/").at(-1) ?? "";
  const codeColor = getCodeColor(filename);

  // The diff is only meaningful for the file currently shown.
  const editForFile = activeEdit?.path === filePath ? activeEdit : null;
  const diff = useMemo<DiffLine[] | null>(() => {
    if (!editForFile) return null;
    return editForFile.kind === "str_replace"
      ? lineDiff(editForFile.oldStr, editForFile.newStr)
      : lineDiff("", editForFile.content);
  }, [editForFile]);
  const stats = useMemo(() => (diff ? diffStats(diff) : null), [diff]);

  // Default to Diff while an edit is fresh; let the user flip to the full file.
  const [mode, setMode] = useState<"diff" | "file">("diff");
  useEffect(() => {
    if (editForFile) setMode("diff");
  }, [editForFile]);
  const showDiff = mode === "diff" && diff !== null;

  // Auto-scroll while writing
  useEffect(() => {
    if (isWriting) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [content, isWriting, showDiff]);

  if (!filePath) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <PencilIcon className="text-muted-foreground/30 h-6 w-6" />
        <span className="text-muted-foreground/50 text-xs">
          {t.agentComputer.editor.startWriting}
        </span>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-black/50">
      {/* File header */}
      <div className="border-border/30 flex shrink-0 items-center gap-2 border-b bg-black/40 px-3 py-1.5">
        <CodeIcon className="text-muted-foreground/60 h-3 w-3" />
        <span className="text-muted-foreground/80 truncate font-mono text-xs">
          {filename}
        </span>
        {showDiff && stats && (stats.added > 0 || stats.removed > 0) && (
          <span className="ml-1 shrink-0 font-mono text-[10px]">
            <span className="text-emerald-400">+{stats.added}</span>{" "}
            <span className="text-red-400">−{stats.removed}</span>
          </span>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-1">
          {diff !== null && (
            <div className="border-border/40 flex items-center rounded border text-[10px]">
              <button
                onClick={() => setMode("diff")}
                className={cn(
                  "px-1.5 py-0.5 transition-colors",
                  mode === "diff"
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground/60 hover:text-muted-foreground",
                )}
              >
                {t.agentComputer.editor.diff}
              </button>
              <button
                onClick={() => setMode("file")}
                className={cn(
                  "px-1.5 py-0.5 transition-colors",
                  mode === "file"
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground/60 hover:text-muted-foreground",
                )}
              >
                {t.agentComputer.editor.file}
              </button>
            </div>
          )}
          {!showDiff && lineCount > 1 && (
            <span className="text-muted-foreground/50 text-[10px]">
              {t.agentComputer.editor.lines(lineCount)}
            </span>
          )}
          {isWriting && (
            <span className="inline-flex items-center gap-1 rounded bg-blue-500/20 px-1.5 py-0.5 text-[10px] text-blue-400">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-400" />
              {t.agentComputer.editor.writing}
            </span>
          )}
        </div>
      </div>
      {/* Code content */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {showDiff && diff ? (
          <DiffView lines={diff} />
        ) : exists && content ? (
          <pre
            className={cn(
              "p-3 font-mono text-[11px] leading-relaxed break-all whitespace-pre-wrap",
              codeColor,
            )}
          >
            {content}
            {isWriting && <span className="animate-pulse text-white">█</span>}
          </pre>
        ) : (
          <div className="flex h-full items-center justify-center">
            <LoaderCircleIcon className="text-muted-foreground/30 h-5 w-5 animate-spin" />
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Tab 3: Browser — rendered HTML preview
// ──────────────────────────────────────────────────────────
function Browser({
  threadId,
  filePath,
  devServer,
  devServers = [],
  selectedLabel = "app",
  onSelectLabel,
  onStartPreview,
  hasRunnableProject = false,
  onAgentMessage,
  entryHtmlArtifact = null,
}: {
  threadId: string;
  filePath: string | null;
  devServer: {
    running: boolean;
    status: string;
    port: number | null;
    url: string | null;
    compiles?: number;
  };
  devServers?: { label: string; running: boolean }[];
  selectedLabel?: string;
  onSelectLabel?: (label: string) => void;
  onStartPreview?: (label?: string) => void | Promise<void>;
  hasRunnableProject?: boolean;
  onAgentMessage?: (text: string) => void;
  entryHtmlArtifact?: string | null;
}) {
  const { t } = useI18n();
  const { content, exists } = useSandboxFile(threadId, filePath);
  const [viewMode, setViewMode] = useState<"desktop" | "mobile">("desktop");
  const [reloadKey, setReloadKey] = useState(0);
  const filename = filePath?.split("/").at(-1) ?? "";
  const isHtml = filename.endsWith(".html") || filename.endsWith(".htm");

  // ── Browser chrome: address bar + back/forward over our own history stack ──
  // The proxied app runs at an opaque origin (no allow-same-origin), so we can't
  // observe in-app link clicks; the address bar drives navigation to a route and
  // back/forward replay the routes we pushed. Routes reset when the server/label
  // changes (prefix changes).
  const prefix = (devServer.url ?? "").replace(/\/$/, "");
  // For a single static HTML deliverable whose entry isn't index.html, default the
  // preview route to that file so the dev-server root ("/") doesn't 404 to a blank
  // white page. Framework dev servers ship no raw .html artifact → entryRoute "/".
  const entryRoute = useMemo(() => {
    const base = (entryHtmlArtifact ?? "").split("/").at(-1) ?? "";
    return /\.html?$/i.test(base) && base.toLowerCase() !== "index.html"
      ? `/${base}`
      : "/";
  }, [entryHtmlArtifact]);
  const [navStack, setNavStack] = useState<string[]>([entryRoute]);
  const [navIdx, setNavIdx] = useState(0);
  const route = navStack[navIdx] ?? "/";
  const [routeInput, setRouteInput] = useState(entryRoute);
  useEffect(() => setRouteInput(route), [route]);
  useEffect(() => {
    // New dev server / switched label / new deliverable → fresh history at entry route.
    setNavStack([entryRoute]);
    setNavIdx(0);
  }, [prefix, entryRoute]);
  const goRoute = useCallback(
    (raw: string) => {
      let p = raw.trim();
      if (!p) p = "/";
      if (!p.startsWith("/")) p = "/" + p;
      setNavStack((s) => [...s.slice(0, navIdx + 1), p]);
      setNavIdx((i) => i + 1);
    },
    [navIdx],
  );
  const canBack = navIdx > 0;
  const canFwd = navIdx < navStack.length - 1;
  const liveSrc = `${getBackendBaseURL()}${prefix}${route}`;

  // Live VNC view of the sandbox's real browser (watch the agent browse).
  const [showVnc, setShowVnc] = useState(false);
  const { vnc: vncUrl } = useSandboxTerminalUrl(threadId, showVnc);

  // Agent browser self-test (native sandbox Chromium): console errors + screenshot.
  const {
    result: manualTest,
    running: selfTesting,
    run: runSelfTest,
  } = useBrowserCheck(threadId);
  // The deterministic auto-check runs on every preview — poll it so results show
  // without anyone clicking. Manual run (if any) takes precedence.
  const autoTest = useLastBrowserCheck(threadId, devServer.running);
  const selfTest =
    manualTest ?? (autoTest && autoTest.routes.length > 0 ? autoTest : null);
  const [showSelfTest, setShowSelfTest] = useState(false);
  // Surface automatically when the self-test found issues.
  useEffect(() => {
    if (selfTest && !selfTest.ok && selfTest.routes.length > 0)
      setShowSelfTest(true);
  }, [selfTest]);
  const triggerSelfTest = useCallback(() => {
    setShowSelfTest(true);
    void runSelfTest(selectedLabel, route);
  }, [runSelfTest, selectedLabel, route]);

  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!content || !isHtml) {
      setBlobUrl(null);
      return;
    }
    const blob = new Blob([content], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    setBlobUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [content, isHtml]);

  // Download the HTML file (blob URL is same-origin; never window.open it).
  const openInNewTab = useCallback(() => {
    if (!content || !filename) return;
    const blob = new Blob([content], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }, [content, filename]);

  // Open the LIVE dev server preview in a real tab (proxy sets CSP sandbox + strips cookies).
  const openLiveInNewTab = useCallback(() => {
    if (devServer.url) window.open(liveSrc, "_blank");
  }, [devServer.url, liveSrc]);

  // Pseudo-HMR: auto-reload the preview iframe each time the dev server recompiles.
  const compiles = devServer.compiles ?? 0;
  useEffect(() => {
    if (compiles > 0) setReloadKey((k) => k + 1);
  }, [compiles]);

  // Always-on preview: when a runnable project exists but no server is up, bring
  // it up deterministically (find project → install → start) — no click, no LLM.
  // Fire once; if it stops later the user can use the manual button.
  const autoStartedRef = useRef(false);
  useEffect(() => {
    if (
      hasRunnableProject &&
      !devServer.running &&
      onStartPreview &&
      !autoStartedRef.current
    ) {
      autoStartedRef.current = true;
      void onStartPreview(selectedLabel);
    }
    if (devServer.running) autoStartedRef.current = true;
  }, [hasRunnableProject, devServer.running, onStartPreview, selectedLabel]);

  // ── VNC live view: watch the agent's real browser ──
  if (showVnc) {
    return (
      <div className="flex h-full flex-col">
        <div className="border-border/30 bg-muted/20 flex shrink-0 items-center gap-1.5 border-b px-2 py-1">
          <button
            onClick={() => setShowVnc(false)}
            className="text-muted-foreground hover:text-foreground rounded px-1 text-sm"
            title={t.agentComputer.browser.back}
          >
            ‹
          </button>
          <span className="text-muted-foreground/70 font-mono text-xs">
            {t.agentComputer.browser.watchLiveBrowser} · VNC
          </span>
        </div>
        <div className="min-h-0 flex-1 bg-black">
          {vncUrl ? (
            <iframe
              key={vncUrl}
              src={vncUrl}
              title={t.agentComputer.browser.watchAgentBrowser}
              className="h-full w-full border-0"
            />
          ) : (
            <div className="flex h-full items-center justify-center">
              <LoaderCircleIcon className="text-muted-foreground/30 h-5 w-5 animate-spin" />
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── HIGHEST PRIORITY: live dev server is running → iframe the proxy ──
  if (devServer.running && devServer.url) {
    return (
      <div className="flex h-full flex-col">
        {/* Browser chrome: nav + address bar */}
        <div className="border-border/30 bg-muted/20 flex shrink-0 items-center gap-1 border-b px-1.5 py-1">
          <button
            onClick={() => canBack && setNavIdx((i) => i - 1)}
            disabled={!canBack}
            className={cn(
              "rounded px-1 text-sm transition-colors",
              canBack
                ? "text-muted-foreground hover:text-foreground"
                : "text-muted-foreground/25",
            )}
            title={t.agentComputer.browser.back}
          >
            ‹
          </button>
          <button
            onClick={() => canFwd && setNavIdx((i) => i + 1)}
            disabled={!canFwd}
            className={cn(
              "rounded px-1 text-sm transition-colors",
              canFwd
                ? "text-muted-foreground hover:text-foreground"
                : "text-muted-foreground/25",
            )}
            title={t.agentComputer.browser.forward}
          >
            ›
          </button>
          <button
            onClick={() => setReloadKey((k) => k + 1)}
            className="text-muted-foreground/60 hover:text-foreground rounded px-1 transition-colors"
            title={t.agentComputer.browser.reload}
          >
            ⟳
          </button>
          <span
            className={cn(
              "ml-0.5 h-2 w-2 shrink-0 rounded-full",
              devServer.status === "ready"
                ? "bg-emerald-400"
                : "animate-pulse bg-yellow-400",
            )}
            title={
              devServer.status === "ready"
                ? t.agentComputer.browser.live
                : t.agentComputer.browser.compiling
            }
          />
          <form
            onSubmit={(e) => {
              e.preventDefault();
              goRoute(routeInput);
            }}
            className="border-border/40 bg-background/40 flex min-w-0 flex-1 items-center rounded-md border px-2"
          >
            <span className="text-muted-foreground/40 shrink-0 font-mono text-[10px] select-none">
              :{devServer.port}
            </span>
            <input
              value={routeInput}
              onChange={(e) => setRouteInput(e.target.value)}
              spellCheck={false}
              className="text-muted-foreground/90 focus:text-foreground min-w-0 flex-1 bg-transparent px-1.5 py-0.5 font-mono text-[11px] focus:outline-none"
              placeholder="/"
            />
          </form>
          {devServers.length > 1 && (
            <select
              value={selectedLabel}
              onChange={(e) => onSelectLabel?.(e.target.value)}
              className="border-border/40 bg-muted/30 text-muted-foreground shrink-0 rounded border px-1 py-0.5 font-mono text-[10px] focus:outline-none"
              title={t.agentComputer.browser.switchPreview}
            >
              {devServers.map((s) => (
                <option key={s.label} value={s.label}>
                  {s.label}
                </option>
              ))}
            </select>
          )}
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              onClick={triggerSelfTest}
              disabled={selfTesting}
              className={cn(
                "rounded p-1 transition-colors",
                selfTesting
                  ? "text-primary"
                  : selfTest && selfTest.routes.length > 0
                    ? selfTest.ok
                      ? "text-emerald-400"
                      : "text-red-400"
                    : "text-muted-foreground/50 hover:text-muted-foreground",
              )}
              title={t.agentComputer.browser.selfTest}
            >
              {selfTesting ? (
                <LoaderCircleIcon className="h-3 w-3 animate-spin" />
              ) : (
                <EyeIcon className="h-3 w-3" />
              )}
            </button>
            <button
              onClick={() => setShowVnc(true)}
              className="text-muted-foreground/50 hover:text-muted-foreground rounded px-1 py-0.5 font-mono text-[9px] transition-colors"
              title={t.agentComputer.browser.watchAgentBrowser}
            >
              VNC
            </button>
            <button
              onClick={() => setViewMode("desktop")}
              className={cn(
                "rounded p-1 transition-colors",
                viewMode === "desktop"
                  ? "text-foreground bg-muted"
                  : "text-muted-foreground/50 hover:text-muted-foreground",
              )}
              title={t.agentComputer.browser.desktop}
            >
              <MonitorIcon className="h-3 w-3" />
            </button>
            <button
              onClick={() => setViewMode("mobile")}
              className={cn(
                "rounded p-1 transition-colors",
                viewMode === "mobile"
                  ? "text-foreground bg-muted"
                  : "text-muted-foreground/50 hover:text-muted-foreground",
              )}
              title={t.agentComputer.browser.mobile}
            >
              <span className="font-mono text-[10px]">📱</span>
            </button>
            <button
              onClick={openLiveInNewTab}
              className="text-muted-foreground/50 hover:text-muted-foreground rounded p-1 transition-colors"
              title={t.agentComputer.browser.openNewTab}
            >
              <ExternalLinkIcon className="h-3 w-3" />
            </button>
          </div>
        </div>
        {/* Self-test results strip */}
        {showSelfTest && (
          <div className="border-border/30 bg-muted/10 shrink-0 border-b px-2 py-1.5 text-[11px]">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "font-medium",
                  selfTesting
                    ? "text-primary"
                    : selfTest?.ok
                      ? "text-emerald-400"
                      : "text-red-400",
                )}
              >
                {selfTesting
                  ? t.agentComputer.browser.testingInBrowser
                  : selfTest?.ok
                    ? t.agentComputer.browser.selfTestPassed
                    : t.agentComputer.browser.selfTestIssues}
              </span>
              {selfTest?.port != null && (
                <span className="text-muted-foreground/50 font-mono text-[10px]">
                  {t.agentComputer.browser.testedPort(String(selfTest.port))}
                </span>
              )}
              {selfTest &&
                !selfTest.ok &&
                selfTest.reason &&
                selfTest.routes.length === 0 && (
                  <span className="text-muted-foreground/60">
                    {selfTest.reason}
                  </span>
                )}
              <button
                onClick={() => setShowSelfTest(false)}
                className="text-muted-foreground/40 hover:text-muted-foreground ml-auto"
              >
                <XIcon className="h-3 w-3" />
              </button>
            </div>
            {(selfTest?.routes ?? []).map((r, i) => (
              <div key={i} className="mt-1 flex items-start gap-2">
                {r.screenshot && (
                  <a
                    href={r.screenshot}
                    target="_blank"
                    rel="noreferrer"
                    className="shrink-0"
                  >
                    {}
                    <img
                      src={r.screenshot}
                      alt={`screenshot ${r.route}`}
                      className="border-border/40 h-14 w-24 rounded border object-cover object-top"
                    />
                  </a>
                )}
                <div className="min-w-0 flex-1">
                  <span
                    className={cn(
                      "font-mono",
                      r.ok ? "text-emerald-400" : "text-red-400",
                    )}
                  >
                    {r.ok ? "✓" : "✗"} {r.route}
                  </span>
                  <span className="text-muted-foreground/50 ml-1">
                    [{r.status}]
                  </span>
                  {r.console_errors.slice(0, 3).map((ce, j) => (
                    <div
                      key={j}
                      className="truncate font-mono text-[10px] text-red-300/80"
                      title={ce}
                    >
                      {ce}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        <div
          className={cn(
            "bg-muted/10 flex min-h-0 flex-1 justify-center overflow-hidden",
            viewMode === "mobile" ? "py-2" : "",
          )}
        >
          {devServer.status === "ready" ? (
            <iframe
              key={`${reloadKey}-${navIdx}`}
              src={liveSrc}
              title={t.agentComputer.browser.livePreview}
              // No allow-same-origin: the preview is served on the app origin, so an
              // opaque-origin sandbox prevents the agent-built app from reaching the
              // parent app's cookies / localStorage / auth.
              sandbox="allow-scripts allow-forms allow-popups allow-modals"
              className={cn(
                "border-0 bg-white",
                viewMode === "mobile"
                  ? "h-full w-[375px] rounded-lg shadow-lg"
                  : "h-full w-full",
              )}
            />
          ) : (
            <div className="flex h-full w-full flex-col items-center justify-center gap-3">
              <LoaderCircleIcon className="text-muted-foreground/40 h-6 w-6 animate-spin" />
              <p className="text-muted-foreground/50 text-xs">
                {t.agentComputer.browser.devServerCompiling}
              </p>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (!filePath) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
        <GlobeIcon className="text-muted-foreground/20 h-8 w-8" />
        <p className="text-muted-foreground/50 text-xs">
          {t.agentComputer.browser.previewWillAppear}
          <br />
          {t.agentComputer.browser.previewWillAppearLine2}
        </p>
        <button
          onClick={() => setShowVnc(true)}
          className="border-border/40 text-muted-foreground hover:text-foreground mt-1 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition-colors hover:border-[--primary]/40"
        >
          <EyeIcon className="h-3 w-3" />{" "}
          {t.agentComputer.browser.watchLiveBrowser}
        </button>
      </div>
    );
  }

  if (!isHtml) {
    // Non-HTML project (Next.js, React, etc.) — show project info instead
    const ext = filename.split(".").at(-1)?.toLowerCase() ?? "";
    const projectType = ["tsx", "ts", "jsx", "js"].includes(ext)
      ? t.agentComputer.browser.projectType.react
      : ext === "py"
        ? t.agentComputer.browser.projectType.python
        : ext === "md"
          ? t.agentComputer.browser.projectType.markdown
          : t.agentComputer.browser.projectType.code;
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
        <CodeIcon className="h-8 w-8 text-blue-400/30" />
        <div>
          <p className="text-muted-foreground text-xs font-medium">
            {t.agentComputer.browser.projectLabel(projectType)}
          </p>
          <p className="text-muted-foreground/60 mt-1 font-mono text-[11px]">
            {filename}
          </p>
          <p className="text-muted-foreground/50 mt-2 text-[11px] leading-relaxed">
            {t.agentComputer.browser.switchToEditorPrefix}{" "}
            <span className="text-blue-400">{t.agentComputer.tabs.editor}</span>{" "}
            {t.agentComputer.browser.switchToEditorSuffix}
          </p>
          {(onStartPreview ?? onAgentMessage) && (
            <button
              onClick={() => {
                // Deterministic path: backend runs find-project → install → start.
                // Fall back to nudging the agent only if the runner isn't wired.
                if (onStartPreview) void onStartPreview(selectedLabel);
                else
                  onAgentMessage?.(
                    `Start the dev server (npm install if needed, then start_dev_server) so I can preview the running app in the ${t.agentComputer.tabs.browser} tab.`,
                  );
              }}
              className="bg-primary text-primary-foreground mt-3 inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-opacity hover:opacity-90"
            >
              <GlobeIcon className="h-3 w-3" />
              {t.agentComputer.browser.startLivePreview}
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Browser toolbar */}
      <div className="border-border/30 bg-muted/20 flex shrink-0 items-center gap-1.5 border-b px-2 py-1">
        <GlobeIcon className="h-3 w-3 shrink-0 text-orange-400" />
        <span className="text-muted-foreground/70 min-w-0 flex-1 truncate font-mono text-xs">
          {filename}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <button
            onClick={() => setViewMode("desktop")}
            className={cn(
              "rounded p-1 transition-colors",
              viewMode === "desktop"
                ? "text-foreground bg-muted"
                : "text-muted-foreground/50 hover:text-muted-foreground",
            )}
          >
            <MonitorIcon className="h-3 w-3" />
          </button>
          <button
            onClick={() => setViewMode("mobile")}
            className={cn(
              "rounded p-1 transition-colors",
              viewMode === "mobile"
                ? "text-foreground bg-muted"
                : "text-muted-foreground/50 hover:text-muted-foreground",
            )}
          >
            <span className="font-mono text-[10px]">📱</span>
          </button>
          <button
            onClick={openInNewTab}
            className="text-muted-foreground/50 hover:text-muted-foreground rounded p-1 transition-colors"
            title={t.agentComputer.browser.openNewTab}
          >
            <DownloadIcon className="h-3 w-3" />
          </button>
        </div>
      </div>
      {/* iframe */}
      <div
        className={cn(
          "bg-muted/10 flex min-h-0 flex-1 justify-center overflow-hidden",
          viewMode === "mobile" ? "py-2" : "",
        )}
      >
        {blobUrl ? (
          <iframe
            key={blobUrl}
            src={blobUrl}
            sandbox="allow-scripts allow-forms"
            title={`Preview: ${filename}`}
            className={cn(
              "border-0 bg-white",
              viewMode === "mobile"
                ? "h-full w-[375px] rounded-lg shadow-lg"
                : "h-full w-full",
            )}
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-4 text-center">
            {exists ? (
              <LoaderCircleIcon className="text-muted-foreground/30 h-5 w-5 animate-spin" />
            ) : (
              <>
                <GlobeIcon className="text-muted-foreground/20 h-6 w-6" />
                <p className="text-muted-foreground/60 text-xs">
                  {t.agentComputer.browser.fileMissing(filename)}
                </p>
                {onStartPreview && (
                  <button
                    onClick={() => void onStartPreview(selectedLabel)}
                    className="border-border/40 text-muted-foreground hover:text-foreground mt-1 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition-colors"
                  >
                    <GlobeIcon className="h-3 w-3" />
                    {t.agentComputer.browser.startLivePreview}
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Tab 4: Activity — compact event cards + Files tree
// ──────────────────────────────────────────────────────────
function getToolMeta(type: string): { icon: React.ReactNode; color: string } {
  switch (type) {
    case "write_file":
      return {
        icon: <PencilIcon className="h-3 w-3" />,
        color: "text-blue-400",
      };
    case "str_replace":
      return {
        icon: <PencilIcon className="h-3 w-3" />,
        color: "text-purple-400",
      };
    case "read_file":
      return {
        icon: <FileTextIcon className="h-3 w-3" />,
        color: "text-sky-400",
      };
    case "search_files":
    case "grep_files":
      return {
        icon: <FileSearchIcon className="h-3 w-3" />,
        color: "text-orange-400",
      };
    case "scaffold_project":
      return {
        icon: <FolderOpenIcon className="h-3 w-3" />,
        color: "text-indigo-400",
      };
    case "task":
      return {
        icon: <SquareTerminalIcon className="h-3 w-3" />,
        color: "text-indigo-400",
      };
    default:
      return {
        icon: <TerminalIcon className="h-3 w-3" />,
        color: "text-emerald-400",
      };
  }
}

function ActivityEventCard({
  event,
  index,
}: {
  event: AgentActivityEvent;
  index?: number;
}) {
  const meta = getToolMeta(event.type);
  const filename = event.path?.split("/").at(-1);
  const isRunning = event.status === "running";
  const isError = event.status === "error";
  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.25,
        ease: "easeOut",
        delay: typeof index === "number" ? Math.min(index, 8) * 0.04 : 0,
      }}
      className={cn(
        "rounded border px-2 py-1.5 text-xs",
        isError
          ? "border-red-500/20 bg-red-500/5"
          : "border-border/20 bg-muted/10",
      )}
    >
      <div className="flex items-center gap-1.5">
        <span className={cn("shrink-0", meta.color)}>{meta.icon}</span>
        <span className={cn("font-mono text-[11px] font-medium", meta.color)}>
          {event.type}
        </span>
        {filename && (
          <span className="text-muted-foreground/60 truncate text-[10px]">
            {filename}
          </span>
        )}
        <span className="text-muted-foreground/40 ml-auto shrink-0 text-[10px]">
          {event.ts}
        </span>
        {isRunning && (
          <LoaderCircleIcon className="text-muted-foreground/40 h-2.5 w-2.5 animate-spin" />
        )}
      </div>
      {event.summary && (
        <div className="text-muted-foreground/50 mt-0.5 truncate pl-5 text-[10px]">
          {event.summary}
        </div>
      )}
    </motion.div>
  );
}

function getFileIcon(name: string): React.ReactNode {
  const ext = name.split(".").at(-1)?.toLowerCase() ?? "";
  if (ext === "html" || ext === "htm")
    return <GlobeIcon className="h-3 w-3 text-orange-400" />;
  if (ext === "css" || ext === "scss")
    return <PaletteIcon className="h-3 w-3 text-pink-400" />;
  if (ext === "js" || ext === "ts" || ext === "jsx" || ext === "tsx")
    return <CodeIcon className="h-3 w-3 text-yellow-400" />;
  if (ext === "json") return <BracesIcon className="h-3 w-3 text-green-400" />;
  if (ext === "md" || ext === "txt")
    return <FileTextIcon className="h-3 w-3 text-sky-400" />;
  return <FileIcon className="text-muted-foreground/60 h-3 w-3" />;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  return `${(bytes / 1024).toFixed(1)}KB`;
}

type FileTreeNode = {
  name: string;
  children: Record<string, FileTreeNode>;
  file?: SandboxFile;
};

function buildFileTree(files: SandboxFile[]): FileTreeNode {
  const root: FileTreeNode = { name: "workspace", children: {} };
  for (const file of files) {
    const rel = file.virtual_path.replace(
      /^\/mnt\/user-data\/workspace\/?/,
      "",
    );
    const parts = rel.split("/").filter(Boolean);
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i]!;
      node.children[part] ??= { name: part, children: {} };
      node = node.children[part]!;
      if (i === parts.length - 1) node.file = file;
    }
  }
  return root;
}

// Folders first, then files — both alphabetical — so the tree reads like a real repo.
function sortTreeNodes(nodes: FileTreeNode[]): FileTreeNode[] {
  return [...nodes].sort((a, b) => {
    const aIsFolder = !a.file && Object.keys(a.children).length > 0 ? 0 : 1;
    const bIsFolder = !b.file && Object.keys(b.children).length > 0 ? 0 : 1;
    if (aIsFolder !== bIsFolder) return aIsFolder - bIsFolder;
    return a.name.localeCompare(b.name);
  });
}

function FileTreeNode({
  node,
  depth = 0,
  onSelect,
}: {
  node: FileTreeNode;
  depth?: number;
  onSelect: (f: SandboxFile) => void;
}) {
  const [open, setOpen] = useState(true);
  const hasChildren = Object.keys(node.children).length > 0;
  if (!node.file && hasChildren) {
    return (
      <div>
        <button
          onClick={() => setOpen((v) => !v)}
          className="text-muted-foreground hover:bg-muted/30 flex w-full items-center gap-1 rounded px-1 py-0.5 text-[11px] transition-colors"
          style={{ paddingLeft: `${depth * 10 + 4}px` }}
        >
          {open ? (
            <FolderOpenIcon className="h-2.5 w-2.5 shrink-0 text-yellow-400" />
          ) : (
            <FolderIcon className="h-2.5 w-2.5 shrink-0 text-yellow-400" />
          )}
          <span className="font-medium">{node.name}/</span>
        </button>
        {open &&
          sortTreeNodes(Object.values(node.children)).map((child) => (
            <FileTreeNode
              key={child.name}
              node={child}
              depth={depth + 1}
              onSelect={onSelect}
            />
          ))}
      </div>
    );
  }
  if (node.file) {
    return (
      <button
        onClick={() => onSelect(node.file!)}
        className="hover:bg-muted/30 flex w-full items-center gap-1 rounded px-1 py-0.5 text-[11px] transition-colors"
        style={{ paddingLeft: `${depth * 10 + 4}px` }}
      >
        {getFileIcon(node.name)}
        <span className="text-foreground truncate font-mono">{node.name}</span>
        <span className="text-muted-foreground/40 ml-auto shrink-0">
          {formatBytes(node.file.size)}
        </span>
      </button>
    );
  }
  return null;
}

// ──────────────────────────────────────────────────────────
// Tab: Files — the repo tree + Outputs (deliverables). The code browser.
// ──────────────────────────────────────────────────────────
function FilesPanel({
  files,
  artifacts,
  onSelectFile,
  onSelectArtifact,
  threadId,
  runningEvents,
}: {
  files: SandboxFile[];
  artifacts: string[];
  onSelectFile: (f: SandboxFile) => void;
  onSelectArtifact: (path: string) => void;
  threadId: string;
  runningEvents: AgentActivityEvent[];
}) {
  const { t } = useI18n();
  const tree = useMemo(() => buildFileTree(files), [files]);
  const runningCount = runningEvents.filter((e) => TERMINAL_TOOLS.has(e.type)).length;

  const handleDownloadZip = useCallback(() => {
    const url = `${getBackendBaseURL()}/api/sandbox/download-zip?thread_id=${encodeURIComponent(threadId)}`;
    window.location.href = url;
  }, [threadId]);
  if (files.length === 0 && artifacts.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <FolderIcon className="text-muted-foreground/30 h-6 w-6" />
        <span className="text-muted-foreground/50 text-xs">
          {t.agentComputer.files.empty}
        </span>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2 p-2">
      {/* Outputs (presented deliverables) */}
      {artifacts.length > 0 && (
        <div>
          <div className="flex items-center justify-between px-1 pb-1">
            <div className="text-muted-foreground/70 flex items-center gap-1.5 text-[11px] font-medium">
              <FileTextIcon className="h-3 w-3 text-emerald-400" />
              Outputs
              <span className="bg-muted rounded px-1 text-[10px]">
                {artifacts.length}
              </span>
            </div>
            <button
              onClick={handleDownloadZip}
              className="text-muted-foreground/60 hover:text-foreground flex items-center gap-1 rounded px-1 py-0.5 text-[10px] transition-colors"
              title={t.agentComputer.downloadAllZip}
            >
              <DownloadIcon className="h-3 w-3" />
              {t.agentComputer.downloadAllZip}
            </button>
          </div>
          <div className="border-border/20 bg-muted/10 rounded border p-1">
            {artifacts.map((path) => (
              <button
                key={path}
                onClick={() => onSelectArtifact(path)}
                className="hover:bg-muted/30 flex w-full items-center gap-1 rounded px-1 py-0.5 text-[11px] transition-colors"
              >
                {getFileIcon(path.split("/").at(-1) ?? "")}
                <span className="text-foreground truncate font-mono">
                  {path.split("/").at(-1)}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
      {/* Repository tree */}
      <div>
        <div className="flex items-center gap-1.5 px-1 pb-1 text-[11px] font-medium">
          <FolderIcon className="h-3 w-3 text-yellow-400" />
          <span className="text-muted-foreground/70">Repository</span>
          <span className="bg-muted rounded px-1 text-[10px]">
            {files.length}
          </span>
          {runningCount > 0 && (
            <div className="ml-auto flex items-center gap-1.5">
              <LoaderCircleIcon className="text-muted-foreground/60 h-2.5 w-2.5 animate-spin" aria-hidden />
              <span className="text-muted-foreground/60 text-[10px]">
                {runningCount} running
              </span>
            </div>
          )}
        </div>
        {runningCount > 0 && (
          <Progress
            value={0}
            className="mb-1 h-0.5"
            aria-label="Agent is working"
          />
        )}
        <div className="border-border/20 bg-muted/10 rounded border p-1">
          {sortTreeNodes(Object.values(tree.children)).map((child) => (
            <FileTreeNode
              key={child.name}
              node={child}
              depth={0}
              onSelect={onSelectFile}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Tab: Activity — deduped high-level action timeline + export (absorbs Audit).
// Excludes raw bash/search/grep (the Terminal tab owns those) so there's no
// Terminal/Activity duplication.
// ──────────────────────────────────────────────────────────

// Compact pill that surfaces the most recent deterministic verify_result
// at the top of the Activity tab. Sourced from the verify_result custom
// event emitted by the backend's auto-verify-on-present_files gate.
function LlmErrorBadge({ event }: { event: LlmError }) {
  const reason = (event.reason || "unknown").toLowerCase();
  const tone =
    reason === "quota"
      ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
      : reason === "auth"
        ? "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300"
        : "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300";
  const label =
    reason === "quota"
      ? "Last turn failed: out of quota"
      : reason === "auth"
        ? "Last turn failed: auth error"
        : reason === "busy" || reason === "transient"
          ? "Last turn failed: provider busy"
          : "Last turn failed";
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "border-border/30 flex shrink-0 items-center gap-2 border-b px-3 py-2 font-mono text-xs",
        tone,
      )}
      data-testid="llm-error-badge"
      title={event.detail || event.error_type || ""}
    >
      <AlertTriangleIcon className="h-3.5 w-3.5" aria-hidden />
      <span className="truncate">{label}</span>
      <span className="text-muted-foreground/70 ml-auto">
        {event.error_type}
      </span>
    </div>
  );
}

function VerifyResultPill({ event }: { event: VerifyResult }) {
  const { t } = useI18n();
  const ok = event.ok;
  const failedRoutes = (event.routes ?? []).filter((r) => !r.ok);
  const summary = ok
    ? t.agentComputer.verifyResult.passed(event.routes?.length ?? 0)
    : t.agentComputer.verifyResult.failed(failedRoutes.length);
  const tone = ok
    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
    : "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300";
  return (
    <div
      className={cn(
        "border-border/30 flex shrink-0 items-center gap-2 border-b px-3 py-2 font-mono text-xs",
        tone,
      )}
      data-testid="verify-result-pill"
    >
      <span aria-hidden>{ok ? "✓" : "✗"}</span>
      <span className="truncate">{summary}</span>
      {event.console_errors_count > 0 ? (
        <span className="text-muted-foreground/70 ml-auto">
          {t.agentComputer.verifyResult.consoleErrors(
            event.console_errors_count,
          )}
        </span>
      ) : null}
    </div>
  );
}

function ActivityPanel({
  events,
  threadId,
  verifyResult,
}: {
  events: AgentActivityEvent[];
  threadId: string;
  verifyResult?: VerifyResult | null;
}) {
  const { t } = useI18n();
  const bottomRef = useRef<HTMLDivElement>(null);
  const timeline = useMemo(
    () => events.filter((e) => !TERMINAL_TOOLS.has(e.type)),
    [events],
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [timeline.length]);

  return (
    <div className="flex h-full flex-col">
      {verifyResult ? <VerifyResultPill event={verifyResult} /> : null}
      <div className="border-border/30 bg-muted/20 flex shrink-0 items-center justify-between border-b px-2 py-1">
        <span className="text-muted-foreground/70 font-mono text-xs">
          {t.agentComputer.activity.title(timeline.length)}
        </span>
        <a
          href={sandboxAuditDownloadUrl(threadId)}
          download
          className="text-muted-foreground/60 hover:text-foreground rounded p-1 transition-colors"
          title={t.agentComputer.activity.exportAuditLog}
        >
          <DownloadIcon className="h-3 w-3" />
        </a>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-1 p-2">
          {timeline.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-6 text-center">
              <FileTextIcon className="text-muted-foreground/30 h-5 w-5" />
              <span className="text-muted-foreground/50 text-xs">
                {t.agentComputer.activity.empty}
              </span>
            </div>
          ) : (
            timeline.map((event, i) => (
              <ActivityEventCard key={i} index={i} event={event} />
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Task checklist
// ──────────────────────────────────────────────────────────
function TaskChecklist({
  todos,
  taskProgress,
  activityEvents,
}: {
  todos: Todo[];
  taskProgress: TaskProgress | null;
  activityEvents: AgentActivityEvent[];
}) {
  const { t } = useI18n();
  if (todos.length === 0) return null;

  // Enrich todo status from activity events (subagent task completions)
  const completedTaskCount = activityEvents.filter(
    (e) => e.type === "task" && e.status === "done",
  ).length;
  const enrichedTodos = todos.map((todo, i) =>
    i < completedTaskCount ? { ...todo, status: "completed" as const } : todo,
  );

  const done = enrichedTodos.filter((t) => t.status === "completed").length;
  const total = enrichedTodos.length;
  const step = taskProgress?.step ?? done;
  const totalSteps = taskProgress?.total ?? total;
  const pct = totalSteps > 0 ? (step / totalSteps) * 100 : 0;

  return (
    <div className="flex flex-col gap-1 px-3 py-2">
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground/70 text-xs font-medium">
          {t.agentComputer.taskProgress}
        </span>
        <span className="text-muted-foreground/50 text-xs">
          {step} / {totalSteps}
        </span>
      </div>
      <Progress value={pct} className="h-1" />
      <div className="mt-0.5 flex flex-col gap-0.5">
        {enrichedTodos.map((todo, i) => {
          const isCompleted = todo.status === "completed";
          const isInProgress = todo.status === "in_progress";
          return (
            <div
              key={i}
              className={cn(
                "flex items-start gap-1.5 rounded px-1 py-0.5 text-xs",
                isInProgress && "bg-muted/40",
              )}
            >
              {isCompleted ? (
                <CheckCircle2Icon className="mt-px h-3 w-3 shrink-0 text-emerald-500" />
              ) : isInProgress ? (
                <LoaderCircleIcon className="text-primary/70 mt-px h-3 w-3 shrink-0 animate-spin" />
              ) : (
                <CircleIcon className="text-muted-foreground/30 mt-px h-3 w-3 shrink-0" />
              )}
              <span
                className={cn(
                  "text-[11px] leading-snug",
                  isCompleted
                    ? "text-muted-foreground/40 line-through"
                    : isInProgress
                      ? "text-primary/70"
                      : "text-muted-foreground/70",
                )}
              >
                {todo.content}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Tab button
// ──────────────────────────────────────────────────────────
// ──────────────────────────────────────────────────────────
// Tab: Audit — full tool-call trail + JSONL export (compliance / cowork)
// ──────────────────────────────────────────────────────────
// ──────────────────────────────────────────────────────────
// Tab: Review — deterministic dual-audience code review (non-coder + developer)
// ──────────────────────────────────────────────────────────
function riskColor(level: string): string {
  if (level === "high") return "text-red-400";
  if (level === "med") return "text-orange-400";
  return "text-yellow-400";
}

function ReviewPanel({
  threadId,
  review,
  isFetching,
  onRegenerate,
}: {
  threadId: string;
  review: SandboxReview | undefined;
  isFetching: boolean;
  onRegenerate: () => void;
}) {
  const { t } = useI18n();
  const risks = review?.risks ?? [];
  const high = risks.filter((r) => r.level === "high").length;
  const med = risks.filter((r) => r.level === "med").length;
  const verdict = !review
    ? { text: t.agentComputer.review.generating, cls: "text-muted-foreground" }
    : high > 0
      ? { text: t.agentComputer.review.needsLook, cls: "text-red-400" }
      : med > 0
        ? { text: t.agentComputer.review.mostlyFine, cls: "text-orange-400" }
        : { text: t.agentComputer.review.looksClean, cls: "text-emerald-400" };

  return (
    <div className="flex h-full flex-col">
      <div className="border-border/30 bg-muted/20 flex shrink-0 items-center justify-between border-b px-2 py-1">
        <span className="text-muted-foreground/70 font-mono text-xs">
          {t.agentComputer.review.codeReview}
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={onRegenerate}
            disabled={isFetching}
            className="text-muted-foreground/60 hover:text-foreground rounded p-1 transition-colors disabled:opacity-40"
            title={t.agentComputer.review.regenerate}
          >
            {isFetching ? (
              <LoaderCircleIcon className="h-3 w-3 animate-spin" />
            ) : (
              <span className="text-[11px]">⟳</span>
            )}
          </button>
          <a
            href={sandboxReviewDownloadUrl(threadId)}
            download
            className="text-muted-foreground/60 hover:text-foreground rounded p-1 transition-colors"
            title={t.agentComputer.review.download}
          >
            <DownloadIcon className="h-3 w-3" />
          </a>
        </div>
      </div>
      <ScrollArea className="h-full">
        <div className="flex flex-col gap-3 p-3 text-xs">
          {/* Plain-English verdict */}
          <div className="border-border/30 bg-muted/10 rounded-lg border p-3">
            <div className={cn("text-sm font-medium", verdict.cls)}>
              {verdict.text}
            </div>
            {review && (
              <div className="text-muted-foreground/70 mt-1 text-[11px]">
                {review.files.length} file{review.files.length === 1 ? "" : "s"}{" "}
                changed ·{" "}
                <span className="text-emerald-400">+{review.added_total}</span>{" "}
                <span className="text-red-400">−{review.removed_total}</span>
                {high + med === 0 &&
                  ` · ${t.agentComputer.review.noRiskyActions}`}
              </div>
            )}
          </div>

          {/* Risks */}
          {risks.length > 0 && (
            <div>
              <div className="text-muted-foreground/70 mb-1 font-medium">
                {t.agentComputer.review.riskFlags}
              </div>
              <div className="flex flex-col gap-1">
                {risks.map((r, i) => (
                  <div
                    key={i}
                    className="border-border/20 bg-muted/10 rounded border px-2 py-1"
                  >
                    <span
                      className={cn(
                        "font-mono text-[10px] uppercase",
                        riskColor(r.level),
                      )}
                    >
                      {r.level}
                    </span>{" "}
                    <span className="text-muted-foreground/90">
                      {r.message}
                    </span>
                    {r.evidence && (
                      <div className="text-muted-foreground/50 mt-0.5 truncate font-mono text-[10px]">
                        {r.evidence}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Changed files */}
          {review && review.files.length > 0 && (
            <div>
              <div className="text-muted-foreground/70 mb-1 font-medium">
                {t.agentComputer.review.changedFiles}
              </div>
              <div className="flex flex-col gap-px font-mono text-[11px]">
                {review.files.slice(0, 200).map((f, i) => (
                  <div
                    key={i}
                    className="hover:bg-muted/30 flex items-center gap-2 rounded px-1 py-0.5"
                  >
                    <span className="text-muted-foreground/80 min-w-0 flex-1 truncate">
                      {f.path}
                    </span>
                    <span className="text-muted-foreground/40 shrink-0 text-[9px]">
                      {f.status}
                    </span>
                    <span className="shrink-0 text-emerald-400">
                      +{f.added}
                    </span>
                    <span className="shrink-0 text-red-400">−{f.removed}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Checks */}
          {review && Object.keys(review.checks).length > 0 && (
            <div>
              <div className="text-muted-foreground/70 mb-1 font-medium">
                {t.agentComputer.review.detectedChecks}
              </div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(review.checks).map(([name, state]) => (
                  <span
                    key={name}
                    className="border-border/20 bg-muted/10 text-muted-foreground/70 rounded border px-1.5 py-0.5 text-[10px]"
                  >
                    {state === "ok" ? "✅" : state === "warn" ? "⚠️" : "•"}{" "}
                    {name.replace(/_/g, " ")}
                  </span>
                ))}
              </div>
            </div>
          )}

          {review?.files.length === 0 && (
            <div className="text-muted-foreground/50">
              {t.agentComputer.review.noChanges}
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Skills launcher — one-click run a skill on the current workspace, in-panel.
// ──────────────────────────────────────────────────────────
const SKILL_DEFAULT_TASK: Record<string, string> = {
  "qa-tester": "test the current project and propose fixes for any failures",
  "code-reviewer": "review my latest changes and flag blockers",
  "file-organizer": "organize the workspace and propose a clean structure",
  "deep-research": "research the topic I describe next",
};

// Every enabled skill gets a useful seeded task — explicit override first, then a
// category-aware default so newly-added skills still read well (not a vague nudge).
function defaultTaskFor(s: Skill): string {
  const explicit = SKILL_DEFAULT_TASK[s.name];
  if (explicit) return explicit;
  const c = `${s.category ?? ""} ${s.name}`.toLowerCase();
  if (c.includes("research"))
    return "research the topic I describe next and summarize the findings";
  if (c.includes("test") || c.includes("qa"))
    return "test the current project and report any failures";
  if (c.includes("review") || c.includes("audit") || c.includes("security"))
    return "review my latest changes in the workspace and flag any blockers";
  if (c.includes("organize") || c.includes("file"))
    return "organize the workspace and propose a clean structure";
  if (c.includes("doc"))
    return "document the current project (README + key files)";
  if (c.includes("deploy"))
    return "prepare the current project for deployment and list the steps";
  return "apply this skill to the current workspace";
}

function SkillLauncher({ onRun }: { onRun: (text: string) => void }) {
  const { skills } = useSkills();
  const enabled = skills.filter((s) => s.enabled);
  if (enabled.length === 0) return null;
  // Featured (work-oriented) first.
  const ordered = [...enabled].sort((a, b) => {
    const order = Object.keys(SKILL_DEFAULT_TASK);
    const ai = order.indexOf(a.name);
    const bi = order.indexOf(b.name);
    return (
      (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi) ||
      a.name.localeCompare(b.name)
    );
  });
  return (
    <DropdownMenu>
      <Tooltip content="Run a skill on this workspace">
        <DropdownMenuTrigger asChild>
          <Button size="icon-sm" variant="ghost" className="h-6 w-6">
            <SparklesIcon className="h-3.5 w-3.5" />
          </Button>
        </DropdownMenuTrigger>
      </Tooltip>
      <DropdownMenuContent
        align="end"
        className="max-h-80 w-64 overflow-y-auto"
      >
        {ordered.map((s) => (
          <DropdownMenuItem
            key={s.name}
            onClick={() => onRun(`/${s.name} ${defaultTaskFor(s)}`)}
            className="flex flex-col items-start gap-0.5"
          >
            <span className="font-mono text-xs">/{s.name}</span>
            <span className="text-muted-foreground line-clamp-2 text-[10px] leading-snug">
              {s.description}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

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
      className={cn(
        "relative flex shrink-0 items-center gap-1 px-2.5 py-1.5 text-[11px] font-medium transition-colors",
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
// Privacy Panel (iGIN0)
// ──────────────────────────────────────────────────────────

function PrivacyPanel() {
  const { t } = useI18n();
  const { data: status, isLoading } = useIGINOStatus();
  const toggleMutation = useToggleIGINO();

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center p-4">
        <LoaderCircleIcon className="text-muted-foreground h-4 w-4 animate-spin" />
      </div>
    );
  }

  if (!status) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center p-4 text-sm">
        {t.common.loading}
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 p-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldIcon className="text-primary h-4 w-4" />
            <span className="text-sm font-medium">
              {t.agentComputer.privacy.title}
            </span>
          </div>
          <Switch
            checked={status.enabled}
            onCheckedChange={(checked) => toggleMutation.mutate(checked)}
            disabled={toggleMutation.isPending}
            aria-label={t.agentComputer.privacy.toggleLabel}
            data-testid="igino-toggle"
          />
        </div>

        {/* Source Health */}
        <div className="space-y-2">
          <h3 className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
            {t.agentComputer.privacy.sourceHealth}
          </h3>
          <div className="grid grid-cols-2 gap-2">
            <StatusCard
              label={t.agentComputer.privacy.searxng}
              status={
                status.searxng_healthy
                  ? t.agentComputer.privacy.healthy
                  : t.agentComputer.privacy.unhealthy
              }
              healthy={status.searxng_healthy}
            />
            <StatusCard
              label={t.agentComputer.privacy.tor}
              status={
                status.tor_available
                  ? t.agentComputer.privacy.available
                  : t.agentComputer.privacy.unavailable
              }
              healthy={status.tor_available}
            />
          </div>
        </div>

        {/* Cache Stats */}
        <div className="space-y-2">
          <h3 className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
            {t.agentComputer.privacy.cache}
          </h3>
          <div className="grid grid-cols-3 gap-2">
            <MetricCard
              label={t.agentComputer.privacy.size}
              value={`${status.cache?.size ?? 0}/${status.cache?.max_size ?? 0}`}
            />
            <MetricCard
              label={t.agentComputer.privacy.hitRate}
              value={`${((status.cache?.hit_rate ?? 0) * 100).toFixed(1)}%`}
            />
            <MetricCard
              label={t.agentComputer.privacy.ttl}
              value={`${status.cache?.ttl_s ?? 0}s`}
            />
          </div>
        </div>

        {/* Audit Stats */}
        <div className="space-y-2">
          <h3 className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
            {t.agentComputer.privacy.audit}
          </h3>
          <div className="grid grid-cols-3 gap-2">
            <MetricCard
              label={t.agentComputer.privacy.total}
              value={status.audit?.total_records ?? 0}
            />
            <MetricCard
              label={t.agentComputer.privacy.errors}
              value={status.audit?.errors ?? 0}
            />
            <MetricCard
              label={t.agentComputer.privacy.torUsage}
              value={status.audit?.tor_usage ?? 0}
            />
          </div>
        </div>

        {/* Error */}
        {status.error && (
          <div className="bg-destructive/10 text-destructive rounded-md p-3 text-xs">
            {status.error}
          </div>
        )}
      </div>
    </ScrollArea>
  );
}

function StatusCard({
  label,
  status,
  healthy,
}: {
  label: string;
  status: string;
  healthy: boolean;
}) {
  return (
    <div className="border-border/50 rounded-md border p-2">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div
        className={cn(
          "mt-1 text-sm font-medium",
          healthy ? "text-emerald-400" : "text-amber-400",
        )}
      >
        {status}
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="border-border/50 rounded-md border p-2">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 text-sm font-medium">{value}</div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Main panel
// ──────────────────────────────────────────────────────────
export interface AgentComputerPanelProps {
  threadId: string;
  currentTool: string | null;
  isLoading: boolean;
  todos: Todo[];
  taskProgress: TaskProgress | null;
  verifyResult?: VerifyResult | null;
  messages: Message[];
  activityEvents: AgentActivityEvent[];
  activeWriteFilePath: string | null;
  artifacts?: string[];
  onClose: () => void;
  onAgentMessage?: (text: string) => void;
}

export function AgentComputerPanel({
  threadId,
  currentTool,
  isLoading,
  todos,
  taskProgress,
  verifyResult,
  messages,
  activityEvents,
  activeWriteFilePath,
  artifacts = [],
  onClose,
  onAgentMessage,
}: AgentComputerPanelProps) {
  const { t } = useI18n();
  // Context fallback for verifyResult when the caller doesn't pass it
  // explicitly. The chat page sets the context state via onVerifyResult;
  // we read it here so the Activity pill always has the latest value.
  // This panel is always rendered inside a ThreadContext.Provider
  // (chat-box.tsx), so useThread() is safe here.
  const threadContext = useThread();
  const contextVerifyResult = threadContext.verifyResult;
  const contextLlmError = threadContext.llmError;
  const effectiveVerifyResult = verifyResult ?? contextVerifyResult;
  const effectiveLlmError = contextLlmError;
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

  // sandbox.log SSE is the source of truth (real bash output, all tools).
  // Merge with in-flight "running" LangGraph events for the live spinner.
  const sseEvents = useSandboxLogs(threadId);
  const mergedEvents = useMemo<AgentActivityEvent[]>(() => {
    const fromLog: AgentActivityEvent[] = sseEvents.map(
      (e: SandboxEvent, i) => ({
        id: `log-${i}-${e.ts}`,
        ts: e.ts,
        type: e.type,
        path: e.path,
        summary: e.summary,
        output: e.output,
        status: "done",
      }),
    );
    const seen = new Set(
      sseEvents.map((e) => `${e.ts}|${e.type}|${e.summary}`),
    );
    const running = activityEvents.filter(
      (e) =>
        e.status === "running" && !seen.has(`${e.ts}|${e.type}|${e.summary}`),
    );
    return [...fromLog, ...running];
  }, [sseEvents, activityEvents]);

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
      if (!data.exists || !data.content) return;
      const filename = target.split("/").at(-1) ?? "file";
      const blob = new Blob([data.content], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      /* silent */
    }
  }, [threadId, browserFilePath, files]);

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
    TERMINAL_TOOLS.has(e.type),
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
        {(isLoading || currentTool) && (
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
              <AuroraText
                colors={["#8b5cf6", "#a78bfa", "#06b6d4", "#8b5cf6"]}
                speed={1.5}
                className="text-[10px] font-medium"
              >
                {t.agentComputer.live}
              </AuroraText>
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
          {/* Secondary actions collapsed into one overflow menu (enterprise: no icon soup) */}
          {(Boolean(onAgentMessage) || files.length > 0) && (
            <DropdownMenu>
              <Tooltip content="More actions">
                <DropdownMenuTrigger asChild>
                  <Button size="icon-sm" variant="ghost" className="h-6 w-6">
                    <MoreHorizontalIcon className="h-3.5 w-3.5" />
                  </Button>
                </DropdownMenuTrigger>
              </Tooltip>
              <DropdownMenuContent align="end" className="w-48">
                {onAgentMessage && (
                  <DropdownMenuItem onClick={handleGitHubPush}>
                    <GithubIcon className="mr-2 h-3.5 w-3.5" />{" "}
                    {t.agentComputer.pushToGithub}
                  </DropdownMenuItem>
                )}
                {files.length > 0 && (
                  <>
                    <DropdownMenuItem onClick={handleDownloadZip}>
                      <FolderOpenIcon className="mr-2 h-3.5 w-3.5" />{" "}
                      {t.agentComputer.downloadAllZip}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={handleDownload}>
                      <DownloadIcon className="mr-2 h-3.5 w-3.5" />{" "}
                      {t.agentComputer.downloadActiveFile}
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          <Tooltip content="Close">
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={onClose}
              className="h-6 w-6"
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
      <div className="border-border/50 scrollbar-none flex shrink-0 overflow-x-auto border-b">
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

      {/* ── Tab content ── */}
      <div className="min-h-0 flex-1 overflow-hidden">
        <AgentComputerErrorBoundary tabName="Files">
          {activeTab === "files" ? (
            <ScrollArea className="h-full">
              <FilesPanel
                files={files}
                artifacts={artifacts}
                onSelectFile={handleSelectFile}
                onSelectArtifact={handleSelectArtifact}
                threadId={threadId}
                runningEvents={mergedEvents}
              />
            </ScrollArea>
          ) : null}
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Terminal">
          {activeTab === "terminal" ? (
            <Terminal events={mergedEvents} threadId={threadId} />
          ) : null}
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Editor">
          {activeTab === "editor" ? (
            <Editor
              threadId={threadId}
              filePath={derivedFilePath}
              isWriting={
                currentTool === "write_file" || currentTool === "str_replace"
              }
              activeTab={activeTab === "editor"}
              activeEdit={activeEdit}
            />
          ) : null}
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Browser">
          {activeTab === "browser" ? (
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
            />
          ) : null}
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Review">
          {activeTab === "review" ? (
            <ReviewPanel
              threadId={threadId}
              review={reviewQuery.data}
              isFetching={reviewQuery.isFetching}
              onRegenerate={() => void reviewQuery.refetch()}
            />
          ) : null}
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Activity">
          {activeTab === "activity" ? (
            <ActivityPanel
              events={mergedEvents}
              threadId={threadId}
              verifyResult={effectiveVerifyResult}
            />
          ) : null}
        </AgentComputerErrorBoundary>

        <AgentComputerErrorBoundary tabName="Privacy">
          {activeTab === "privacy" ? <PrivacyPanel /> : null}
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
