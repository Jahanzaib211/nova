"use client";

import { SparklesIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/workspace/tooltip";
import { useSkills } from "@/core/skills/hooks";
import type { Skill } from "@/core/skills/type";

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

export function SkillLauncher({ onRun }: { onRun: (text: string) => void }) {
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
