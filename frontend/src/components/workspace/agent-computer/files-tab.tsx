"use client";

import { BracesIcon, CodeIcon, FileIcon, FileTextIcon, FolderIcon, FolderOpenIcon, GlobeIcon, LoaderCircleIcon, PaletteIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { Progress } from "@/components/ui/progress";
import { useI18n } from "@/core/i18n/hooks";
import { type SandboxFile } from "@/core/sandbox/hooks";
import type { AgentActivityEvent } from "@/core/threads/hooks";

import { TERMINAL_TOOLS } from "./terminal-tab";

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
export function FilesPanel({
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

