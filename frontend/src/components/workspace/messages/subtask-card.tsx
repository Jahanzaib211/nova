"use client";

import {
  CheckCircleIcon,
  ChevronUp,
  ClipboardListIcon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { ClipboardSafeStreamdown } from "@/components/ai-elements/streamdown";
import { Button } from "@/components/ui/button";
import { ShineBorder } from "@/components/ui/shine-border";
import { useI18n } from "@/core/i18n/hooks";
import { hasToolCalls } from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { streamdownPluginsWithWordAnimation } from "@/core/streamdown";
import { useSubtask } from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";
import { explainLastToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";
import { FlipDisplay } from "../flip-display";

import { MarkdownContent } from "./markdown-content";

/**
 * What the collapsed row says.
 *
 * It used to render the bare status enum, so three very different outcomes were
 * one identical red "Subtask failed": the subagent ran and failed, the run was
 * cut off before it reported, and -- the one that cost days -- the `task` tool
 * was never bound at all, so nothing ran. That last case arrives as
 * "Error: task is not a valid tool, try one of [...]", which names the actual
 * problem, and the row threw it away. `message-list.tsx` already computes the
 * distinction and stores it on `task.error`; this just stops discarding it.
 *
 * Superseded rows keep their plain label -- they are not a failure and are
 * deliberately styled neutral elsewhere in this file.
 */
function failureSummary(
  task: Subtask,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const label = t.subtasks[task.status];
  if (task.status !== "failed" || !task.error) return label;
  if (task.error === "superseded by a newer run") return label;
  // One line, no wrapper noise: the row is truncated at 420px and the full
  // text is already in the expanded body.
  const reason = (
    task.error.replace(/^Error:\s*/i, "").split("\n")[0] ?? ""
  ).trim();
  return reason ? `${label} — ${reason}` : label;
}

export function SubtaskCard({
  className,
  taskId,
  isLoading,
}: {
  className?: string;
  taskId: string;
  isLoading: boolean;
}) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(true);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  // Not `useSubtask(taskId)!`. The render loop in MessageList iterates
  // `tool_calls` independently of the loop that populates the store, so the
  // entry can genuinely be absent for a render -- and the non-null assertion
  // turned that into a TypeError on `task.status` rather than a blank card.
  const task = useSubtask(taskId);
  const icon = useMemo(() => {
    if (task?.status === "completed") {
      return <CheckCircleIcon className="size-3" />;
    } else if (task?.status === "failed") {
      return <XCircleIcon className="size-3 text-red-500" />;
    } else if (task?.status === "in_progress") {
      return <Loader2Icon className="size-3 animate-spin" />;
    }
  }, [task?.status]);

  // A card with no store entry has nothing to render; bail before the JSX so
  // every access below stays on the non-optional `task`.
  if (!task) return null;

  return (
    <ChainOfThought
      className={cn("relative w-full gap-2 rounded-lg border py-0", className)}
      open={!collapsed}
    >
      <div
        className={cn(
          "ambilight z-[-1]",
          task.status === "in_progress" ? "enabled" : "",
        )}
      ></div>
      {task.status === "in_progress" && (
        <>
          <ShineBorder
            borderWidth={1.5}
            shineColor={["#A07CFE", "#FE8FB5", "#FFBE7B"]}
          />
        </>
      )}
      {/* Stable seam for tests. The visible status lives inside FlipDisplay,
          which animates between values by splitting text across elements, so
          asserting on the rendered label is inherently racy. */}
      <div
        className="bg-background/95 flex w-full flex-col rounded-lg"
        data-testid="subtask-card"
        data-status={task.status}
      >
        <div className="flex w-full items-center justify-between p-0.5">
          <Button
            className="w-full items-start justify-start text-left"
            variant="ghost"
            onClick={() => setCollapsed(!collapsed)}
          >
            <div className="flex w-full items-center justify-between">
              <ChainOfThoughtStep
                className="font-normal"
                label={
                  task.status === "in_progress" ? (
                    <Shimmer duration={3} spread={3}>
                      {task.description}
                    </Shimmer>
                  ) : (
                    task.description
                  )
                }
                icon={<ClipboardListIcon />}
              ></ChainOfThoughtStep>
              <div className="flex items-center gap-1">
                {collapsed && (
                  <div
                    className={cn(
                      "text-muted-foreground flex items-center gap-1 text-xs font-normal",
                      task.status === "failed" &&
                        task.error !== "superseded by a newer run"
                        ? "text-red-500 opacity-67"
                        : "",
                    )}
                  >
                    {icon}
                    <FlipDisplay
                      className="max-w-[420px] truncate pb-1"
                      uniqueKey={task.latestMessage?.id ?? ""}
                    >
                      {task.status === "in_progress" &&
                      task.latestMessage &&
                      hasToolCalls(task.latestMessage)
                        ? explainLastToolCall(task.latestMessage, t)
                        : failureSummary(task, t)}
                    </FlipDisplay>
                  </div>
                )}
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4",
                    !collapsed ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
        </div>
        <ChainOfThoughtContent className="px-4 pb-4">
          {task.prompt && (
            <ChainOfThoughtStep
              label={
                <ClipboardSafeStreamdown
                  {...streamdownPluginsWithWordAnimation}
                  components={{ a: CitationLink }}
                >
                  {task.prompt}
                </ClipboardSafeStreamdown>
              }
            ></ChainOfThoughtStep>
          )}
          {task.status === "in_progress" &&
            task.latestMessage &&
            hasToolCalls(task.latestMessage) && (
              <ChainOfThoughtStep
                label={t.subtasks.in_progress}
                icon={<Loader2Icon className="size-4 animate-spin" />}
              >
                {explainLastToolCall(task.latestMessage, t)}
              </ChainOfThoughtStep>
            )}
          {task.status === "completed" && (
            <>
              <ChainOfThoughtStep
                label={t.subtasks.completed}
                icon={<CheckCircleIcon className="size-4" />}
              ></ChainOfThoughtStep>
              <ChainOfThoughtStep
                label={
                  task.result ? (
                    // Clamp: one verbose subagent result used to stretch the
                    // whole chat column until refresh. Scroll, don't spill.
                    <div className="max-h-56 overflow-y-auto pr-1">
                      <MarkdownContent
                        content={task.result}
                        isLoading={false}
                        rehypePlugins={rehypePlugins}
                      />
                    </div>
                  ) : null
                }
              ></ChainOfThoughtStep>
            </>
          )}
          {task.status === "failed" && (
            <ChainOfThoughtStep
              label={
                <div
                  className={cn(
                    task.error === "superseded by a newer run"
                      ? "text-muted-foreground/60"
                      : "text-red-500",
                  )}
                >
                  {task.error}
                </div>
              }
              icon={
                <XCircleIcon
                  className={cn(
                    "size-4",
                    task.error === "superseded by a newer run"
                      ? "text-muted-foreground/40"
                      : "text-red-500",
                  )}
                />
              }
            ></ChainOfThoughtStep>
          )}
        </ChainOfThoughtContent>
      </div>
    </ChainOfThought>
  );
}
