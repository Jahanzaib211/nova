"use client";

import {
  BracesIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CodeIcon,
  FileIcon,
  FileTextIcon,
  FolderIcon,
  FolderOpenIcon,
  GlobeIcon,
  LoaderCircleIcon,
  PaletteIcon,
  PlayIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Progress } from "@/components/ui/progress";
import { useI18n } from "@/core/i18n/hooks";
import { type SandboxFile } from "@/core/sandbox/hooks";
import type { AgentActivityEvent } from "@/core/threads/hooks";
import {
  useFileImpact,
  useFileSymbols,
  useWorkspaceCommands,
  useWorkspaceSnapshot,
} from "@/core/workspace/hooks";

import { TERMINAL_TOOLS } from "./terminal-tab";
import { WorkspaceCard } from "./workspace-card";

/** Languages the workspace indexer extracts symbols from. */
const OUTLINE_EXTENSIONS = new Set([
  "py",
  "js",
  "ts",
  "jsx",
  "tsx",
  "go",
  "rs",
  "java",
]);

function fileExtension(name: string): string {
  return name.split(".").at(-1)?.toLowerCase() ?? "";
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
  const root: FileTreeNode = { name: "user-data", children: {} };
  for (const file of files) {
    // Workspace files stay at the top level; outputs/ and uploads/ keep
    // their mount folder so agent deliverables are visible in the tree.
    const rel = file.virtual_path
      .replace(/^\/mnt\/user-data\/workspace\/?/, "")
      .replace(/^\/mnt\/user-data\/?/, "");
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
function countFiles(node: FileTreeNode): number {
  if (node.file) return 1;
  return Object.values(node.children).reduce(
    (sum, child) => sum + countFiles(child),
    0,
  );
}

function sortTreeNodes(nodes: FileTreeNode[]): FileTreeNode[] {
  return [...nodes].sort((a, b) => {
    const aIsFolder = !a.file && Object.keys(a.children).length > 0 ? 0 : 1;
    const bIsFolder = !b.file && Object.keys(b.children).length > 0 ? 0 : 1;
    if (aIsFolder !== bIsFolder) return aIsFolder - bIsFolder;
    return a.name.localeCompare(b.name);
  });
}

function symbolKindIcon(kind: string): React.ReactNode {
  if (kind === "class" || kind === "struct" || kind === "interface")
    return <BracesIcon className="h-2.5 w-2.5 text-purple-400" />;
  return <CodeIcon className="h-2.5 w-2.5 text-sky-400" />;
}

/** Mounted only while a file row is expanded — one fetch per (thread, file). */
function FileSymbolOutline({
  threadId,
  filePath,
  depth,
}: {
  threadId: string;
  filePath: string;
  depth: number;
}) {
  const { t } = useI18n();
  const symbols = useFileSymbols(threadId, filePath);
  const impact = useFileImpact(threadId, filePath);
  if (symbols.length === 0) return null;
  const impactedCommands = impact?.scanned ? impact.commands.length : 0;
  return (
    <div>
      {symbols.map((s) => (
        <div
          key={s.fqn}
          className="text-muted-foreground/80 flex items-center gap-1 px-1 py-0.5 text-[10px]"
          style={{ paddingLeft: `${depth * 10 + 18}px` }}
        >
          {symbolKindIcon(s.kind)}
          <span className="truncate font-mono">{s.name}</span>
          <span className="text-muted-foreground/40 ml-auto shrink-0">
            {s.kind}
          </span>
        </div>
      ))}
      {impactedCommands > 0 && (
        <div
          className="text-muted-foreground/50 flex items-center gap-1 px-1 py-0.5 text-[10px] italic"
          style={{ paddingLeft: `${depth * 10 + 18}px` }}
        >
          <PlayIcon className="h-2.5 w-2.5 shrink-0" aria-hidden />
          {t.agentComputer.workspace.impactFooter(impactedCommands)}
        </div>
      )}
    </div>
  );
}

function FileTreeNode({
  node,
  depth = 0,
  onSelect,
  threadId,
  outlineEnabled,
  path = "",
}: {
  node: FileTreeNode;
  depth?: number;
  onSelect: (f: SandboxFile) => void;
  threadId?: string;
  outlineEnabled?: boolean;
  path?: string;
}) {
  const [open, setOpen] = useState(true);
  const [outlineOpen, setOutlineOpen] = useState(false);
  const hasChildren = Object.keys(node.children).length > 0;
  const nodePath = path ? `${path}/${node.name}` : node.name;
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
          <span className="text-muted-foreground/40 ml-auto shrink-0 text-[10px]">
            {countFiles(node)}
          </span>
        </button>
        {open &&
          sortTreeNodes(Object.values(node.children)).map((child) => (
            <FileTreeNode
              key={child.name}
              node={child}
              depth={depth + 1}
              onSelect={onSelect}
              threadId={threadId}
              outlineEnabled={outlineEnabled}
              path={nodePath}
            />
          ))}
      </div>
    );
  }
  if (node.file) {
    const canOutline = Boolean(
      outlineEnabled &&
      threadId &&
      OUTLINE_EXTENSIONS.has(fileExtension(node.name)),
    );
    return (
      <div>
        <div
          className="hover:bg-muted/30 flex w-full items-center gap-1 rounded px-1 py-0.5 text-[11px] transition-colors"
          style={{ paddingLeft: `${depth * 10 + 4}px` }}
        >
          {canOutline && (
            <button
              onClick={() => setOutlineOpen((v) => !v)}
              aria-label={`Toggle symbols for ${node.name}`}
              aria-expanded={outlineOpen}
              className="text-muted-foreground/60 hover:text-foreground -ml-0.5 shrink-0"
            >
              {outlineOpen ? (
                <ChevronDownIcon className="h-2.5 w-2.5" />
              ) : (
                <ChevronRightIcon className="h-2.5 w-2.5" />
              )}
            </button>
          )}
          <button
            onClick={() => onSelect(node.file!)}
            className="flex min-w-0 flex-1 items-center gap-1"
          >
            {getFileIcon(node.name)}
            <span className="text-foreground truncate font-mono">
              {node.name}
            </span>
            <span className="text-muted-foreground/40 ml-auto shrink-0">
              {formatBytes(node.file.size)}
            </span>
          </button>
        </div>
        {canOutline && outlineOpen && (
          <FileSymbolOutline
            threadId={threadId!}
            filePath={nodePath}
            depth={depth + 1}
          />
        )}
      </div>
    );
  }
  return null;
}

// ──────────────────────────────────────────────────────────
// Tab: Files — the repo tree + Outputs (deliverables). The code browser.
// ──────────────────────────────────────────────────────────
export function FilesPanel({
  files,
  artifacts,
  onSelectFile,
  onSelectArtifact,
  threadId,
  runningEvents,
  active = true,
}: {
  files: SandboxFile[];
  artifacts: string[];
  onSelectFile: (f: SandboxFile) => void;
  onSelectArtifact: (path: string) => void;
  threadId: string;
  runningEvents: AgentActivityEvent[];
  active?: boolean;
}) {
  const { t } = useI18n();
  const tree = useMemo(() => buildFileTree(files), [files]);
  const runningCount = runningEvents.filter((e) =>
    TERMINAL_TOOLS.has(e.type),
  ).length;
  // Workspace intelligence: renders nothing while the backend flag is off.
  // `active` gates the query the same way every other tab does (see
  // agent-computer-panel.tsx's comment on the pattern) — this tab was the
  // one missed, and since all tabs stay mounted (only `hidden` via CSS)
  // while Activity's own useWorkspaceSnapshot call shares this exact query
  // key, an always-enabled observer here kept refetching the snapshot in
  // the background regardless of which tab the user was actually looking
  // at (workspace scans/audit events firing while on Terminal/Browser/
  // Privacy/etc.).
  const workspaceState = useWorkspaceSnapshot(threadId, active);
  const workspaceAvailable =
    workspaceState.availability === "available" &&
    workspaceState.snapshot !== null;
  const commands = useWorkspaceCommands(threadId, workspaceAvailable);

  const hasSandboxContent = files.length > 0 || artifacts.length > 0;

  return (
    <div className="flex flex-col gap-2 p-2">
      <WorkspaceCard state={workspaceState} />
      {!hasSandboxContent && (
        <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
          <FolderIcon className="text-muted-foreground/30 h-6 w-6" />
          <span className="text-muted-foreground/50 text-xs">
            {t.agentComputer.files.empty}
          </span>
        </div>
      )}
      {/* Commands discovered by the workspace indexer (dev/test/lint...) */}
      {commands.length > 0 && (
        <div>
          <div className="text-muted-foreground/70 flex items-center gap-1.5 px-1 pb-1 text-[11px] font-medium">
            <PlayIcon className="h-3 w-3 text-sky-400" />
            Commands
            <span className="bg-muted rounded px-1 text-[10px]">
              {commands.length}
            </span>
          </div>
          <div className="flex flex-wrap gap-1 px-1">
            {commands.map((c) => (
              <span
                key={`${c.project_id}:${c.name}`}
                title={c.argv.join(" ")}
                className="border-border/30 bg-muted/20 text-muted-foreground rounded border px-1.5 py-0.5 font-mono text-[10px]"
              >
                {c.name}
              </span>
            ))}
          </div>
        </div>
      )}
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
      {(files.length > 0 || runningCount > 0) && (
        <div>
          <div className="flex items-center gap-1.5 px-1 pb-1 text-[11px] font-medium">
            <FolderIcon className="h-3 w-3 text-yellow-400" />
            <span className="text-muted-foreground/70">
              {t.agentComputer.files.repository}
            </span>
            <span className="bg-muted rounded px-1 text-[10px]">
              {files.length}
            </span>
            {runningCount > 0 && (
              <div className="ml-auto flex items-center gap-1.5">
                <LoaderCircleIcon
                  className="text-muted-foreground/60 h-2.5 w-2.5 animate-spin"
                  aria-hidden
                />
                <span className="text-muted-foreground/60 text-[10px]">
                  {t.agentComputer.files.running(runningCount)}
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
                threadId={threadId}
                outlineEnabled={workspaceAvailable}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
