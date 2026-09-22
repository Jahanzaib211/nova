"use client";

import { DownloadIcon, LoaderCircleIcon } from "lucide-react";
import { useMemo } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useI18n } from "@/core/i18n/hooks";
import {
  sandboxReviewDownloadUrl,
  type SandboxReview,
} from "@/core/sandbox/hooks";
import { useWorkspaceEvents } from "@/core/workspace/hooks";
import { cn } from "@/lib/utils";

// Tab: Review — deterministic dual-audience code review (non-coder + developer)
// ──────────────────────────────────────────────────────────
function riskColor(level: string): string {
  if (level === "high") return "text-destructive";
  if (level === "med") return "text-warning";
  return "text-warning";
}

// WIK risk levels (deerflow.workspace.models.execution_plan.RiskLevel) use a
// different vocabulary than the sandbox-review risks above.
function kernelRiskColor(level: string): string {
  if (level === "critical") return "text-destructive";
  if (level === "high") return "text-warning";
  if (level === "medium") return "text-warning";
  return "text-success";
}

export function ReviewPanel({
  threadId,
  review,
  isFetching,
  isError = false,
  onRegenerate,
  active = true,
}: {
  threadId: string;
  review: SandboxReview | undefined;
  isFetching: boolean;
  /** The fetch failed outright — distinct from "still generating". Without
   * this the panel showed eternal "Generating…" on a 500, and regenerate
   * silently repeated the same failure. */
  isError?: boolean;
  onRegenerate: () => void;
  active?: boolean;
}) {
  const { t } = useI18n();
  // The review payload crosses the network as an unchecked cast; every field
  // access below must survive a partial/missing body without taking this tab
  // into its error boundary.
  const risks = review?.risks ?? [];
  const changedFiles = review?.files ?? [];
  const addedTotal = review?.added_total ?? 0;
  const removedTotal = review?.removed_total ?? 0;
  const checks = review?.checks ?? {};
  const high = risks.filter((r) => r.level === "high").length;
  const med = risks.filter((r) => r.level === "med").length;
  // Latest WIK plan-built verdict (bus -> SSE bridge); undefined pre-flag
  // or before any plan has been built for this thread.
  // `active` gates the SSE connection. Every tab stays mounted (only `hidden`
  // via CSS), and EventSource has no idle-close — so an ungated call here held
  // an open connection to /api/workspace/{id}/events for the whole life of the
  // thread, on top of Activity's own. Activity already gates its identical
  // call; this one was missed.
  const liveEvents = useWorkspaceEvents(threadId, active);
  const latestPlan = useMemo(
    () =>
      [...liveEvents]
        .reverse()
        .find(
          (e): e is Extract<typeof e, { type: "PlanBuilt" }> =>
            e.type === "PlanBuilt",
        ),
    [liveEvents],
  );
  const verdict = isError
    ? {
        text: t.agentComputer.review.generationFailed,
        cls: "text-destructive",
      }
    : !review
      ? {
          text: t.agentComputer.review.generating,
          cls: "text-muted-foreground",
        }
      : changedFiles.length === 0 && high + med === 0
        ? // A review of nothing is not a clean bill of health; "Looks clean"
          // in green over "0 files changed" claimed a verdict that was never
          // reached.
          {
            text: t.agentComputer.review.noChanges,
            cls: "text-muted-foreground",
          }
        : high > 0
          ? { text: t.agentComputer.review.needsLook, cls: "text-destructive" }
          : med > 0
            ? {
                text: t.agentComputer.review.mostlyFine,
                cls: "text-warning",
              }
            : {
                text: t.agentComputer.review.looksClean,
                cls: "text-success",
              };

  return (
    <div className="flex h-full flex-col">
      <div className="border-panel-border bg-muted/20 flex shrink-0 items-center justify-between border-b px-2 py-1">
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
          <div className="border-panel-border bg-muted/10 rounded-lg border p-3">
            <div className={cn("text-sm font-medium", verdict.cls)}>
              {verdict.text}
            </div>
            {review && changedFiles.length > 0 && (
              <div className="text-muted-foreground/70 mt-1 text-[11px]">
                {changedFiles.length} file{changedFiles.length === 1 ? "" : "s"}{" "}
                changed · <span className="text-success">+{addedTotal}</span>{" "}
                <span className="text-destructive">−{removedTotal}</span>
                {high + med === 0 &&
                  ` · ${t.agentComputer.review.noRiskyActions}`}
              </div>
            )}
            {isError && (
              <button
                type="button"
                onClick={onRegenerate}
                className="text-muted-foreground/60 hover:text-foreground mt-1 block text-[11px] underline underline-offset-2"
              >
                {t.agentComputer.review.regenerate}
              </button>
            )}
          </div>

          {/* WIK plan verdict (live, bus -> SSE) */}
          {latestPlan && (
            <div className="border-panel-border bg-muted/10 rounded-lg border p-3">
              <div className="text-muted-foreground/70 mb-1 font-medium">
                {t.agentComputer.review.kernelVerdictTitle}
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "font-mono text-[10px] uppercase",
                    kernelRiskColor(latestPlan.data?.risk_level),
                  )}
                >
                  {latestPlan.data?.risk_level ?? "?"}
                </span>
                <span className="text-muted-foreground/90">
                  {latestPlan.data?.plan_valid
                    ? t.agentComputer.review.kernelVerdictValid
                    : t.agentComputer.review.kernelVerdictInvalid}
                </span>
                <span className="text-muted-foreground/50">
                  ·{" "}
                  {t.agentComputer.review.kernelVerdictSteps(
                    latestPlan.data?.step_count ?? 0,
                  )}
                </span>
              </div>
            </div>
          )}

          {/* Risks */}
          {risks.length > 0 && (
            <div>
              <div className="text-muted-foreground/70 mb-1 font-medium">
                {t.agentComputer.review.riskFlags}
              </div>
              <div className="flex flex-col gap-1">
                {risks.map((r, i) => (
                  <div
                    key={`${r.level}:${(r.message ?? r.evidence ?? "").slice(0, 48)}:${i}`}
                    className="border-panel-border bg-muted/10 rounded border px-2 py-1"
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
          {review && changedFiles.length > 0 && (
            <div>
              <div className="text-muted-foreground/70 mb-1 font-medium">
                {t.agentComputer.review.changedFiles}
              </div>
              <div className="flex flex-col gap-px font-mono text-[11px]">
                {changedFiles.slice(0, 200).map((f, i) => (
                  <div
                    key={f.path || i}
                    className="hover:bg-muted/30 flex items-center gap-2 rounded px-1 py-0.5"
                  >
                    <span className="text-muted-foreground/80 min-w-0 flex-1 truncate">
                      {f.path}
                    </span>
                    <span className="text-muted-foreground/40 shrink-0 text-[9px]">
                      {f.status}
                    </span>
                    <span className="text-success shrink-0">+{f.added}</span>
                    <span className="text-destructive shrink-0">
                      −{f.removed}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Checks */}
          {review && Object.keys(checks).length > 0 && (
            <div>
              <div className="text-muted-foreground/70 mb-1 font-medium">
                {t.agentComputer.review.detectedChecks}
              </div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(checks).map(([name, state]) => (
                  <span
                    key={name}
                    className="border-panel-border bg-muted/10 text-muted-foreground/70 rounded border px-1.5 py-0.5 text-[10px]"
                  >
                    {state === "ok" ? "✅" : state === "warn" ? "⚠️" : "•"}{" "}
                    {name.replace(/_/g, " ")}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Skills launcher — one-click run a skill on the current workspace, in-panel.
