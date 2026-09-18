"use client";

import {
  BracesIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CodeIcon,
  DownloadIcon,
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
import { urlOfArtifact } from "@/core/artifacts/utils";
import { useI18n } from "@/core/i18n/hooks";
import { type SandboxFile } from "@/core/sandbox/hooks";
import type { AgentActivityEvent } from "@/core/threads/hooks";
import { isTerminalTool } from "@/core/threads/tool-surface";
import { useUploadLimits, summarizeUploadLimits } from "@/core/uploads/hooks";
import {
  useFileImpact,
  useFileSymbols,
  useWorkspaceCommands,
  useWorkspaceSnapshot,
} from "@/core/workspace/hooks";

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
    return <GlobeIcon className="text-warning h-3 w-3" />;
  if (ext === "css" || ext === "scss")
    return <PaletteIcon className="text-info h-3 w-3" />;
  if (ext === "js" || ext === "ts" || ext === "jsx" || ext === "tsx")
    return <CodeIcon className="text-warning h-3 w-3" />;
  if (ext === "json") return <BracesIcon className="text-success h-3 w-3" />;
  if (ext === "md" || ext === "txt")
    return <FileTextIcon className="text-info h-3 w-3" />;
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

/**
 * Files for the repository tree: everything on disk minus what the Outputs
 * section already renders.
 *
 * `GET /api/sandbox/files` walks the workspace, outputs AND uploads roots,
 * while presented deliverables are listed separately as `artifacts` — so every
 * presented file showed up twice in the panel, once per section. Both sections
 * earn their place (deliverables vs. everything on disk); a file just belongs
 * to exactly one. `artifacts` and `virtual_path` share the same
 * `/mnt/user-data/outputs/...` spelling, so an exact match is the right key.
 */
export function selectTreeFiles(
  files: SandboxFile[],
  artifacts: string[],
): SandboxFile[] {
  if (artifacts.length === 0) return files;
  const presented = new Set(artifacts);
  return files.filter((f) => !presented.has(f.virtual_path));
}

/** Mount roots that keep their folder name at the top of the tree. */
const MOUNT_FOLDERS = ["outputs", "uploads"] as const;

/**
 * Virtual path -> the path shown in the tree.
 *
 * Workspace files are flattened to the top level (that is the repo the user
 * cares about); outputs/ and uploads/ keep their mount folder so deliverables
 * stay visible. That flattening puts the workspace's OWN `outputs/` directory
 * into the same namespace as the `/mnt/user-data/outputs` mount, so
 * `workspace/outputs/report.md` and `outputs/report.md` both became
 * `outputs/report.md` — one silently overwrote the other in the tree while the
 * header count still counted both, so the panel claimed N files and drew N-1,
 * and which one survived flipped with mtime ordering.
 *
 * Only the genuinely ambiguous workspace paths get disambiguated, so the common
 * case keeps its flat shape.
 */
const WORKSPACE_PREFIX = /^\/mnt\/user-data\/workspace\/?(.*)$/;

export function treePathOf(virtualPath: string): string {
  const workspace = WORKSPACE_PREFIX.exec(virtualPath);
  if (workspace) {
    const rel = workspace[1] ?? "";
    const head = rel.split("/")[0];
    return MOUNT_FOLDERS.some((f) => f === head) ? `workspace/${rel}` : rel;
  }
  return virtualPath.replace(/^\/mnt\/user-data\/?/, "");
}

function buildFileTree(files: SandboxFile[]): FileTreeNode {
  const root: FileTreeNode = { name: "user-data", children: {} };
  for (const file of files) {
    const parts = treePathOf(file.virtual_path).split("/").filter(Boolean);
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
// A node can be BOTH a file and a folder (a file `build` next to `build/out.js`),
// so children are always summed rather than short-circuited on `node.file` —
// doing that returned 1 and made every descendant uncountable, matching the
// render bug where the whole subtree disappeared.
function countFiles(node: FileTreeNode): number {
  const own = node.file ? 1 : 0;
  return Object.values(node.children).reduce(
    (sum, child) => sum + countFiles(child),
    own,
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
    return <BracesIcon className="text-info h-2.5 w-2.5" />;
  return <CodeIcon className="text-info h-2.5 w-2.5" />;
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
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  const [outlineOpen, setOutlineOpen] = useState(false);
  const hasChildren = Object.keys(node.children).length > 0;
  const nodePath = path ? `${path}/${node.name}` : node.name;
  const canOutline = Boolean(
    node.file &&
    outlineEnabled &&
    threadId &&
    OUTLINE_EXTENSIONS.has(fileExtension(node.name)),
  );

  if (!node.file && !hasChildren) return null;

  // A node can be a file AND a folder at once — a file `build` sitting next to
  // `build/out.js`. Previously the folder branch was skipped whenever
  // `node.file` was set, so the node rendered as a lone file row and every
  // descendant became unreachable. Render both.
  return (
    <div>
      {node.file && (
        <>
          <div
            className="hover:bg-muted/30 group flex w-full items-center gap-1 rounded px-1 py-0.5 text-[11px] transition-colors"
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
            {threadId && (
              <a
                href={urlOfArtifact({
                  filepath: node.file.virtual_path,
                  threadId,
                  download: true,
                })}
                download={node.name}
                onClick={(e) => e.stopPropagation()}
                title={t.common.download}
                aria-label={`${t.common.download} ${node.name}`}
                className="text-muted-foreground/50 hover:text-foreground shrink-0 transition-colors"
              >
                <DownloadIcon className="h-2.5 w-2.5" />
              </a>
            )}
          </div>
          {canOutline && outlineOpen && (
            <FileSymbolOutline
              threadId={threadId!}
              filePath={nodePath}
              depth={depth + 1}
            />
          )}
        </>
      )}
      {hasChildren && (
        <>
          <button
            onClick={() => setOpen((v) => !v)}
            className="text-muted-foreground hover:bg-muted/30 flex w-full items-center gap-1 rounded px-1 py-0.5 text-[11px] transition-colors"
            style={{ paddingLeft: `${depth * 10 + 4}px` }}
          >
            {open ? (
              <FolderOpenIcon className="text-warning h-2.5 w-2.5 shrink-0" />
            ) : (
              <FolderIcon className="text-warning h-2.5 w-2.5 shrink-0" />
            )}
            <span className="font-medium">{node.name}/</span>
            <span className="text-muted-foreground/40 ml-auto shrink-0 text-[10px]">
              {countFiles(node) - (node.file ? 1 : 0)}
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
        </>
      )}
    </div>
  );
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
  // The backend's merge_artifacts reducer already dedupes, but this list is
  // used as a React key — one duplicate from any future writer would collide
  // keys and make rows render/click inconsistently. Cheap to be certain.
  const uniqueArtifacts = useMemo(() => [...new Set(artifacts)], [artifacts]);
  const treeFiles = useMemo(
    () => selectTreeFiles(files, uniqueArtifacts),
    [files, uniqueArtifacts],
  );
  const tree = useMemo(() => buildFileTree(treeFiles), [treeFiles]);
  // Only *in-flight* work counts as running. `mergedEvents` is the full
  // sandbox.log history plus live spinners; filtering by name alone counted
  // every completed command forever, so the header spun and read "N running"
  // on an idle thread.
  const runningCount = runningEvents.filter(
    (e) => e.status === "running" && isTerminalTool(e.type),
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
  const { data: uploadLimits } = useUploadLimits(threadId);
  const limitsSummary = summarizeUploadLimits(uploadLimits);

  return (
    <div className="flex flex-col gap-2 p-2">
      <WorkspaceCard state={workspaceState} />
      {limitsSummary && (
        <p
          className="text-muted-foreground/60 px-1 text-[10px]"
          title={t.agentComputer.files.uploadLimitsHint}
        >
          {t.agentComputer.files.uploadLimits} · {limitsSummary}
        </p>
      )}{" "}
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
            <PlayIcon className="text-info h-3 w-3" />
            {t.agentComputer.files.commandsHeader}
            <span className="bg-muted rounded px-1 text-[10px]">
              {commands.length}
            </span>
          </div>
          <div className="flex flex-wrap gap-1 px-1">
            {commands.map((c) => (
              <span
                key={`${c.project_id}:${c.name}`}
                title={c.argv.join(" ")}
                className="border-panel-border bg-muted/20 text-muted-foreground rounded border px-1.5 py-0.5 font-mono text-[10px]"
              >
                {c.name}
              </span>
            ))}
          </div>
        </div>
      )}
      {/* Outputs (presented deliverables) */}
      {uniqueArtifacts.length > 0 && (
        <div>
          <div className="flex items-center justify-between px-1 pb-1">
            <div className="text-muted-foreground/70 flex items-center gap-1.5 text-[11px] font-medium">
              <FileTextIcon className="text-success h-3 w-3" />
              Outputs
              <span className="bg-muted rounded px-1 text-[10px]">
                {uniqueArtifacts.length}
              </span>
            </div>
          </div>
          <div className="border-panel-border bg-muted/10 rounded border p-1">
            {uniqueArtifacts.map((path) => (
              <div
                key={path}
                className="hover:bg-muted/30 group flex w-full items-center gap-1 rounded px-1 py-0.5 text-[11px] transition-colors"
              >
                <button
                  onClick={() => onSelectArtifact(path)}
                  className="flex min-w-0 flex-1 items-center gap-1"
                >
                  {getFileIcon(path.split("/").at(-1) ?? "")}
                  <span className="text-foreground truncate font-mono">
                    {path.split("/").at(-1)}
                  </span>
                </button>
                <a
                  href={urlOfArtifact({
                    filepath: path,
                    threadId,
                    download: true,
                  })}
                  download={path.split("/").at(-1)}
                  title={t.common.download}
                  aria-label={`${t.common.download} ${path.split("/").at(-1)}`}
                  className="text-muted-foreground/50 hover:text-foreground shrink-0 transition-colors"
                >
                  <DownloadIcon className="h-2.5 w-2.5" />
                </a>
              </div>
            ))}
          </div>
        </div>
      )}
      {/* Repository tree */}
      {(treeFiles.length > 0 || runningCount > 0) && (
        <div>
          <div className="flex items-center gap-1.5 px-1 pb-1 text-[11px] font-medium">
            <FolderIcon className="text-warning h-3 w-3" />
            <span className="text-muted-foreground/70">
              {t.agentComputer.files.repository}
            </span>
            <span className="bg-muted rounded px-1 text-[10px]">
              {treeFiles.length}
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
          <div className="border-panel-border bg-muted/10 rounded border p-1">
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
