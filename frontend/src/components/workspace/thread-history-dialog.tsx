"use client";

import {
  ChevronsUpDownIcon,
  CopyIcon,
  HistoryIcon,
  Loader2Icon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { getAPIClient } from "@/core/api";
import { writeTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";
import { formatTimeAgo } from "@/core/utils/datetime";
import { cn } from "@/lib/utils";

interface CheckpointView {
  checkpointId: string;
  createdAt: string | null;
  step?: number;
  source?: string;
  next: string[];
  title?: string;
  messageCount: number;
  valuesJson: string;
}

function formatValues(values: unknown): {
  title?: string;
  messageCount: number;
  json: string;
} {
  let title: string | undefined;
  let messageCount = 0;
  let json = "";
  try {
    json = JSON.stringify(values, null, 2);
  } catch {
    json = String(values);
  }
  if (values && typeof values === "object") {
    const record = values as Record<string, unknown>;
    if (typeof record.title === "string") title = record.title;
    if (Array.isArray(record.messages)) messageCount = record.messages.length;
  }
  return { title, messageCount, json };
}

export function ThreadHistoryDialog({
  threadId,
  open,
  onOpenChange,
}: {
  threadId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const [checkpoints, setCheckpoints] = useState<CheckpointView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !threadId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCheckpoints([]);
    getAPIClient()
      .threads.getHistory(threadId, { limit: 50 })
      .then((states) => {
        if (cancelled) return;
        setCheckpoints(
          states.map((state) => {
            const { title, messageCount, json } = formatValues(state.values);
            const meta = (state.metadata ?? {}) as Record<string, unknown>;
            // The gateway's /history response carries checkpoint_id at the
            // top level (HistoryEntry model); the SDK type nests it under
            // `checkpoint` — read both so either shape works.
            const raw = state as unknown as {
              checkpoint_id?: string | null;
              created_at?: string | null;
            };
            const created = raw.created_at ?? (state.created_at ?? null);
            return {
              checkpointId:
                raw.checkpoint_id ??
                (state.checkpoint?.checkpoint_id ?? "-"),
              createdAt: created,
              step: typeof meta.step === "number" ? meta.step : undefined,
              source: typeof meta.source === "string" ? meta.source : undefined,
              next: Array.isArray(state.next) ? state.next.map(String) : [],
              title,
              messageCount,
              valuesJson: json,
            };
          }),
        );
      })
      .catch((e) => {
        if (cancelled) return;
        setError(
          e instanceof Error ? e.message : t.settings.threadHistory.loadError,
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, threadId, t]);

  const copyCheckpoint = async (id: string) => {
    const ok = await writeTextToClipboard(id);
    if (ok) toast.success("Checkpoint ID copied.");
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onOpenChange(false);
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <HistoryIcon className="size-4" />
            {t.settings.threadHistory.title}
          </DialogTitle>
          <DialogDescription>
            {t.settings.threadHistory.description}
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2Icon className="size-4 animate-spin" />
            {t.common.loading}
          </div>
        ) : error ? (
          <div className="py-4 text-sm text-destructive">{error}</div>
        ) : checkpoints.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">
            {t.settings.threadHistory.empty}
          </p>
        ) : (
          <ScrollArea className="max-h-[28rem]">
            <ul className="space-y-2 pr-2">
              {checkpoints.map((cp, index) => {
                const isOpen = expanded === cp.checkpointId;
                return (
                  <li
                    key={cp.checkpointId}
                    className="rounded-lg border p-3 text-sm"
                  >
                    <button
                      type="button"
                      className="flex w-full items-center justify-between gap-3 text-left"
                      onClick={() =>
                        setExpanded(isOpen ? null : cp.checkpointId)
                      }
                      aria-expanded={isOpen}
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs">
                            {index === 0
                              ? "(latest) "
                              : `#${checkpoints.length - index - 1} `}
                            {cp.checkpointId.slice(0, 8)}
                          </span>
                          {cp.source && (
                            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                              {cp.source}
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 text-xs text-muted-foreground">
                          {cp.createdAt ? formatTimeAgo(cp.createdAt) : "—"}
                          {cp.step !== undefined && ` · step ${cp.step}`}
                          {cp.messageCount > 0 &&
                            ` · ${cp.messageCount} messages`}
                          {cp.next.length > 0 &&
                            ` · next: ${cp.next.join(", ")}`}
                        </div>
                      </div>
                      <ChevronsUpDownIcon
                        className={cn(
                          "size-4 shrink-0 text-muted-foreground transition-transform",
                          isOpen && "rotate-180",
                        )}
                      />
                    </button>
                    {isOpen && (
                      <div className="mt-3 space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => void copyCheckpoint(cp.checkpointId)}
                          >
                            <CopyIcon className="size-3.5" />
                            {t.settings.threadHistory.copyId}
                          </Button>
                        </div>
                        <pre className="max-h-64 overflow-auto rounded-lg bg-muted/50 p-3 font-mono text-[11px] leading-relaxed">
                          {cp.valuesJson}
                        </pre>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </ScrollArea>
        )}
      </DialogContent>
    </Dialog>
  );
}