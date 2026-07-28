"use client";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import ProgressiveSkillsAnimation from "../progressive-skills-animation";
import { Section } from "../section";

export function SkillsSection({ className }: { className?: string }) {
  const { t } = useI18n();
  return (
    <Section
      // Pinned to the viewport only where the two-pane animation fits; on
      // phones it stacks and needs to grow to its own height instead.
      className={cn("w-full bg-white/2 md:h-[calc(100vh-64px)]", className)}
      title={t.landing.skills.title}
      subtitle={
        <div>
          Agent Skills are loaded progressively — only what&apos;s needed, when
          it&apos;s needed.
          <br />
          Extend Nova with your own skill files, or use our built-in library.
        </div>
      }
    >
      <div className="relative overflow-hidden">
        <ProgressiveSkillsAnimation />
      </div>
    </Section>
  );
}
