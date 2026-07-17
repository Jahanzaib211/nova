"use client";

import { DownloadIcon, LoaderCircleIcon } from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useI18n } from "@/core/i18n/hooks";
import { sandboxReviewDownloadUrl, type SandboxReview } from "@/core/sandbox/hooks";
import { cn } from "@/lib/utils";

// Tab: Review — deterministic dual-audience code review (non-coder + developer)
// ──────────────────────────────────────────────────────────
function riskColor(level: string): string {
  if (level === "high") return "text-red-400";
  if (level === "med") return "text-orange-400";
  return "text-yellow-400";
}

export function ReviewPanel({
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
