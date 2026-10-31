"use client";

import MagicBento, { type BentoCardProps } from "@/components/ui/magic-bento";

import { Section } from "../section";

const COLOR = "#0a0a0a";

const features: BentoCardProps[] = [
  {
    color: COLOR,
    label: "Secure by default",
    title: "40 security tools in every sandbox",
    description: "Recon, vulnerabilities, web apps, secrets and defence.",
  },
  {
    color: COLOR,
    label: "Verify loop",
    title: "Runs the app, checks it in a real browser, reviews the diff",
    description:
      "Console and network errors are read before a result is returned.",
  },
  {
    color: COLOR,
    label: "Any model",
    title:
      "Claude, OpenAI-compatible APIs, DeepSeek, MiniMax, vLLM, or a local model on one GPU",
    description: "Your key, your hardware.",
  },
  {
    color: COLOR,
    label: "Open core",
    title: "MIT-licensed core, self-hosted, full control",
    description: "Run it yourself, or let a partner deploy it.",
  },
  {
    color: COLOR,
    label: "MCP for other agents",
    title: "Other agents reach Nova through scoped, revocable tokens",
    description: "Nothing else is exposed.",
  },
  {
    color: COLOR,
    label: "Isolated tasks",
    title:
      "Every chat thread gets its own container with resource limits and a command log",
    description: "With a tamper-evident audit trail.",
  },
];

export function WhatsNewSection({ className }: { className?: string }) {
  return (
    <Section
      id="whats-new"
      className={className}
      kicker="What ships"
      title="What's new"
      subtitle="Six parts of Nova that are in the product today, not on the roadmap."
    >
      <div className="landing-bento flex w-full items-center justify-center">
        <MagicBento
          data={features}
          glowColor="146, 109, 255"
          textAutoHide={false}
        />
      </div>
    </Section>
  );
}
