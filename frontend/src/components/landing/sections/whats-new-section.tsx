"use client";

import MagicBento, { type BentoCardProps } from "@/components/ui/magic-bento";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { Section } from "../section";

const COLOR = "#0a0a0a";
const features: BentoCardProps[] = [
  {
    color: COLOR,
    label: "Context Engineering",
    title: "Long/Short-term Memory",
    description: "Now the agent can better understand you",
  },
  {
    color: COLOR,
    label: "Long Task Running",
    title: "Planning and Sub-tasking",
    description:
      "Plans ahead, reasons through complexity, then executes sequentially or in parallel",
  },
  {
    color: COLOR,
    label: "Extensible",
    title: "Skills and Tools",
    description:
      "Plug, play, or even swap built-in tools. Build the agent you want.",
  },

  {
    color: COLOR,
    label: "Persistent",
    title: "Sandbox with File System",
    description: "Read, write, run — like a real computer",
  },
  {
    color: COLOR,
    label: "Flexible",
    title: "Multi-Model Support",
    description: "Doubao, DeepSeek, OpenAI, Gemini, etc.",
  },
  {
    color: COLOR,
    label: "Free",
    title: "Open Source",
    description: "MIT License, self-hosted, full control",
  },
];

export function WhatsNewSection({ className }: { className?: string }) {
  const { t } = useI18n();
  return (
    <Section
      className={cn("", className)}
      title={t.landing.whatsNew.title}
      subtitle={t.landing.whatsNew.subtitle}
    >
      <div className="flex w-full items-center justify-center">
        <MagicBento data={features} />
      </div>
    </Section>
  );
}
