"use client";

import { BotIcon, MessageSquareIcon, SparklesIcon, Trash2Icon } from "lucide-react";
import { useRouter } from "next/navigation";
import { type ComponentProps, type ReactElement, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useDeleteAgent, useUpdateAgent } from "@/core/agents";
import type { Agent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { useSkills } from "@/core/skills/hooks";
import { cn } from "@/lib/utils";

interface AgentCardProps {
  agent: Agent;
}

/**
 * Reveals the full text in a tooltip ONLY when its trigger is actually clipped.
 * Clipping is measured on pointer enter against the trigger's own box, covering
 * both single-line `truncate` (width) and multi-line `line-clamp` (height), so
 * untruncated content never pops a redundant tooltip.
 */
function TruncatedTooltip({
  text,
  children,
}: {
  text: string;
  children: ReactElement;
}) {
  const [truncated, setTruncated] = useState(false);
  return (
    <Tooltip>
      <TooltipTrigger
        asChild
        onPointerEnter={(e) => {
          const el = e.currentTarget;
          setTruncated(
            el.scrollWidth > el.clientWidth ||
              el.scrollHeight > el.clientHeight,
          );
        }}
      >
        {children}
      </TooltipTrigger>
      {truncated && (
        <TooltipContent className="max-w-xs text-wrap break-words">
          {text}
        </TooltipContent>
      )}
    </Tooltip>
  );
}

/**
 * Long, user-controlled labels (agent model, skills, tool groups) that must
 * never break the card layout: width is capped to the parent and the text is
 * truncated with an ellipsis, with the full value revealed on hover.
 */
function TruncatedBadge({
  label,
  variant,
  className,
}: {
  label: string;
  variant: ComponentProps<typeof Badge>["variant"];
  className?: string;
}) {
  return (
    <TruncatedTooltip text={label}>
      <Badge
        variant={variant}
        className={cn("block max-w-full truncate", className)}
      >
        {label}
      </Badge>
    </TruncatedTooltip>
  );
}

export function AgentCard({ agent }: AgentCardProps) {
  const { t } = useI18n();
  const router = useRouter();
  const deleteAgent = useDeleteAgent();
  const updateAgent = useUpdateAgent();
  const { skills } = useSkills();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);

  const allSkillNames = useMemo(
    () => skills.filter((s) => s.enabled).map((s) => s.name),
    [skills],
  );
  // Deterministic per-agent skill set: null = inherit all enabled skills.
  // Seed the toggles from the agent's whitelist, falling back to "all on".
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const effectiveSelected = useMemo(
    () => selected ?? new Set(agent.skills ?? allSkillNames),
    [selected, agent.skills, allSkillNames],
  );

  async function handleSaveSkills() {
    try {
      const chosen = [...effectiveSelected];
      // Persist null when every skill is on (inherit), else the explicit
      // whitelist (deterministic node-level scoping; [] = no skills).
      const skillsValue =
        chosen.length === allSkillNames.length &&
        allSkillNames.every((n) => effectiveSelected.has(n))
          ? null
          : chosen;
      await updateAgent.mutateAsync({ name: agent.name, request: { skills: skillsValue } });
      toast.success("Agent skills updated");
      setSkillsOpen(false);
      setSelected(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  function handleChat() {
    router.push(`/workspace/agents/${agent.name}/chats/new`);
  }

  async function handleDelete() {
    try {
      await deleteAgent.mutateAsync(agent.name);
      toast.success(t.agents.deleteSuccess);
      setDeleteOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <Card className="group flex flex-col transition-shadow hover:shadow-md">
        <CardHeader className="pb-3">
          <div className="flex min-w-0 items-start justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <div className="bg-primary/10 text-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                <BotIcon className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <TruncatedTooltip text={agent.name}>
                  <CardTitle className="truncate text-base">
                    {agent.name}
                  </CardTitle>
                </TruncatedTooltip>
                {agent.model && (
                  <TruncatedBadge
                    label={agent.model}
                    variant="secondary"
                    className="mt-0.5 text-xs"
                  />
                )}
              </div>
            </div>
          </div>
          {agent.description && (
            <TruncatedTooltip text={agent.description}>
              <CardDescription className="mt-2 line-clamp-2 text-sm">
                {agent.description}
              </CardDescription>
            </TruncatedTooltip>
          )}
        </CardHeader>

        {(agent.tool_groups?.length ?? agent.skills?.length ?? 0) > 0 && (
          <CardContent className="pt-0 pb-3">
            <div className="flex flex-wrap gap-1">
              {agent.tool_groups?.map((group) => (
                <TruncatedBadge
                  key={`tg:${group}`}
                  label={group}
                  variant="outline"
                  className="text-xs"
                />
              ))}
              {agent.skills?.map((skill) => (
                <TruncatedBadge
                  key={`sk:${skill}`}
                  label={skill}
                  variant="secondary"
                  className="text-xs"
                />
              ))}
            </div>
          </CardContent>
        )}

        <CardFooter className="mt-auto flex items-center justify-between gap-2 pt-3">
          <Button size="sm" className="flex-1" onClick={handleChat}>
            <MessageSquareIcon className="mr-1.5 h-3.5 w-3.5" />
            {t.agents.chat}
          </Button>
          <div className="flex gap-1">
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8 shrink-0"
              onClick={() => setSkillsOpen(true)}
              title={t.a11y.editSkills}
              aria-label={t.a11y.editSkills}
            >
              <SparklesIcon className="h-3.5 w-3.5" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="text-destructive hover:text-destructive h-8 w-8 shrink-0"
              onClick={() => setDeleteOpen(true)}
              title={t.agents.delete}
            >
              <Trash2Icon className="h-3.5 w-3.5" />
            </Button>
          </div>
        </CardFooter>
      </Card>

      {/* Per-agent skill toggles — deterministic node-level skill scoping */}
      <Dialog open={skillsOpen} onOpenChange={(o) => { setSkillsOpen(o); if (!o) setSelected(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.a11y.skillsFor(agent.name)}</DialogTitle>
            <DialogDescription>
              {t.agents.skillsDialogDescription}
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="max-h-80">
            <div className="flex flex-col gap-1 pr-3">
              {allSkillNames.length === 0 ? (
                <p className="text-muted-foreground py-6 text-center text-sm">
                  No skills enabled — enable some in Settings → Skills.
                </p>
              ) : (
                allSkillNames.map((name) => {
                  const on = effectiveSelected.has(name);
                  return (
                    <div key={name} className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5 hover:bg-muted/30">
                      <span className="font-mono text-sm">/{name}</span>
                      <Switch
                        checked={on}
                        onCheckedChange={(checked) => {
                          const next = new Set(effectiveSelected);
                          if (checked) next.add(name); else next.delete(name);
                          setSelected(next);
                        }}
                      />
                    </div>
                  );
                })
              )}
            </div>
          </ScrollArea>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setSkillsOpen(false); setSelected(null); }} disabled={updateAgent.isPending}>
              {t.common.cancel}
            </Button>
            <Button onClick={handleSaveSkills} disabled={updateAgent.isPending}>
              {updateAgent.isPending ? t.common.loading : t.common.save}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirm */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.agents.delete}</DialogTitle>
            <DialogDescription>{t.agents.deleteConfirm}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteOpen(false)}
              disabled={deleteAgent.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleteAgent.isPending}
            >
              {deleteAgent.isPending ? t.common.loading : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
