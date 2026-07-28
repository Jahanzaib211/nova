"use client";

import {
  BoxIcon,
  BracesIcon,
  FolderGit2Icon,
  LoaderCircleIcon,
  RefreshCwIcon,
  SquareTerminalIcon,
} from "lucide-react";

import { Tooltip } from "@/components/workspace/tooltip";
import { useI18n } from "@/core/i18n/hooks";
import type { WorkspaceSnapshotState } from "@/core/workspace/hooks";

/**
 * Workspace intelligence card (C10 items 1+2+17).
 *
 * Sits at the top of the Files tab when the workspace kernel is enabled
 * and has a snapshot: repo kind + primary language, file/symbol/command
 * counts, and the detected projects (contextual grouping). Renders
 * nothing when the kernel is disabled — the plain Files tree remains.
 */
export function WorkspaceCard({ state }: { state: WorkspaceSnapshotState }) {
  const { t } = useI18n();
  const { availability, snapshot, isIndexing, refresh } = state;

  if (availability === "disabled") return null;

  if (availability === "unindexed" || (!snapshot && isIndexing)) {
    return (
      <div className="border-border/20 bg-muted/10 flex items-center justify-between rounded border p-2">
        <span className="text-muted-foreground/70 flex items-center gap-1.5 text-[11px]">
          <FolderGit2Icon className="h-3 w-3 text-sky-400" />
          {t.agentComputer.workspace.title}
        </span>
        <button
          onClick={() => refresh()}
          disabled={isIndexing}
          className="text-muted-foreground/70 hover:text-foreground flex items-center gap-1 text-[10px] transition-colors disabled:opacity-50"
        >
          {isIndexing ? (
            <LoaderCircleIcon className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <RefreshCwIcon className="h-3 w-3" aria-hidden />
          )}
          {isIndexing
            ? t.agentComputer.workspace.indexing
            : t.agentComputer.workspace.index}
        </button>
      </div>
    );
  }

  if (!snapshot) return null;

  return (
    <div className="border-border/20 bg-muted/10 rounded border p-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[11px] font-medium">
          <FolderGit2Icon className="h-3 w-3 text-sky-400" />
          <span className="text-foreground">
            {t.agentComputer.workspace.title}
          </span>
          {snapshot.primary_language ? (
            <span className="bg-muted rounded px-1 text-[10px] capitalize">
              {snapshot.primary_language}
            </span>
          ) : null}
          <span className="text-muted-foreground/60 text-[10px] capitalize">
            {snapshot.repo_kind}
            {snapshot.is_monorepo
              ? ` · ${t.agentComputer.workspace.monorepo}`
              : ""}
          </span>
        </div>
        <Tooltip content={t.agentComputer.workspace.reindex}>
          <button
            onClick={() => refresh(true)}
            disabled={isIndexing}
            className="text-muted-foreground/60 hover:text-foreground transition-colors disabled:opacity-50"
            aria-label={t.agentComputer.workspace.reindex}
          >
            {isIndexing ? (
              <LoaderCircleIcon className="h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <RefreshCwIcon className="h-3 w-3" aria-hidden />
            )}
          </button>
        </Tooltip>
      </div>
      <div className="text-muted-foreground/70 mt-1.5 flex items-center gap-3 text-[10px]">
        <span className="flex items-center gap-1">
          <BoxIcon className="h-2.5 w-2.5" aria-hidden />
          {snapshot.project_count} {t.agentComputer.workspace.projects}
        </span>
        <span className="flex items-center gap-1">
          <BracesIcon className="h-2.5 w-2.5" aria-hidden />
          {snapshot.symbol_count.toLocaleString()}{" "}
          {t.agentComputer.workspace.symbols}
        </span>
        <span className="flex items-center gap-1">
          <SquareTerminalIcon className="h-2.5 w-2.5" aria-hidden />
          {snapshot.command_count} {t.agentComputer.workspace.commands}
        </span>
      </div>
      {snapshot.projects.length > 1 ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {snapshot.projects.slice(0, 6).map((project) => (
            <span
              key={project.project_id}
              className="border-border/30 text-muted-foreground/80 rounded border px-1 py-0.5 text-[10px]"
              title={project.root_path}
            >
              {project.name}
            </span>
          ))}
          {snapshot.projects.length > 6 ? (
            <span className="text-muted-foreground/50 text-[10px]">
              +{snapshot.projects.length - 6}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
