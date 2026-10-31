/**
 * The custom-agent card on /workspace/agents.
 *
 * Three defects are pinned here, all of them silent:
 *
 *  1. The badge block was guarded by
 *     `agent.tool_groups?.length ?? agent.skills?.length ?? 0` — a nullish
 *     chain, not an "or". An agent with `tool_groups: []` (a real backend
 *     value: "no tool groups", as opposed to null = all) evaluated the
 *     chain to 0 and the skills badges never rendered, so the agent's
 *     skill scoping was invisible on the card.
 *  2. The "no skills enabled" copy was a hardcoded English string in a
 *     zh-CN UI.
 *  3. Each skill toggle is a `<button role="switch">` with no label
 *     pointing at it, so screen readers announced an unnamed toggle.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AgentCard } from "@/components/workspace/agents/agent-card";
import type { Agent } from "@/core/agents";
import { I18nProvider } from "@/core/i18n/context";

const { skillsState } = vi.hoisted(() => ({
  skillsState: {
    skills: [
      { name: "pdf-processing", enabled: true },
      { name: "web-search", enabled: true },
    ] as Array<{ name: string; enabled: boolean }>,
  },
}));

vi.mock("@/core/skills/hooks", () => ({
  useSkills: () => skillsState,
}));

vi.mock("@/core/agents", () => ({
  useDeleteAgent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateAgent: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// The skills dialog is closed on first render, so Radix renders nothing.
// Open it for the test so the toggles are part of the markup.
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogContent: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogFooter: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogDescription: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

const agent = (over: Partial<Agent> = {}): Agent => ({
  name: "code-reviewer",
  description: "Reviews code",
  model: null,
  tool_groups: null,
  skills: null,
  ...over,
});

const render = (a: Agent, locale: "en-US" | "zh-CN" = "en-US") =>
  renderToStaticMarkup(
    <I18nProvider initialLocale={locale}>
      <AgentCard agent={a} />
    </I18nProvider>,
  );

/**
 * The tool-group / skill badges the card shows between its header and its
 * footer. (Radix's TooltipTrigger `asChild` overwrites the Badge's own
 * `data-slot`, so the badges are located by the card slots around them.)
 */
function badgeLabels(html: string): string[] {
  const start = html.indexOf('data-slot="card-content"');
  if (start < 0) return [];
  const end = html.indexOf('data-slot="card-footer"', start);
  const region = html.slice(start, end < 0 ? undefined : end);
  return [...region.matchAll(/<span[^>]*>([^<>]+)<\/span>/g)]
    .map((m) => m[1] ?? "")
    .filter(Boolean);
}

describe("agent card badges", () => {
  it("shows the skill badges when the agent has skills but no tool groups", () => {
    // tool_groups: [] is a real value (no tool groups allowed); skills is the
    // whitelist. The old nullish chain read []?.length -> 0 and hid the
    // skills entirely.
    const html = render(agent({ tool_groups: [], skills: ["pdf-processing"] }));
    expect(badgeLabels(html)).toContain("pdf-processing");
  });

  it("shows the tool-group badges", () => {
    const html = render(agent({ tool_groups: ["web"], skills: [] }));
    expect(badgeLabels(html)).toContain("web");
  });

  it("shows neither when the agent has neither", () => {
    const html = render(agent({ tool_groups: [], skills: [] }));
    expect(badgeLabels(html)).toEqual([]);
  });
});

describe("agent card skills dialog", () => {
  it("names every skill toggle", () => {
    const html = render(agent());
    // A <button role="switch"> with no label is an unnamed control.
    expect(html).toContain('for="agent-skill-code-reviewer-pdf-processing"');
    expect(html).toContain('id="agent-skill-code-reviewer-pdf-processing"');
  });

  it("translates the empty state", () => {
    skillsState.skills = [];
    const en = render(agent());
    const zh = render(agent(), "zh-CN");
    expect(en).toContain("No skills enabled");
    expect(zh).toContain("尚未启用任何技能");
    expect(zh).not.toContain("No skills enabled");
    skillsState.skills = [
      { name: "pdf-processing", enabled: true },
      { name: "web-search", enabled: true },
    ];
  });
});

describe("agent card icon buttons", () => {
  it("gives the delete button an accessible name", () => {
    const html = render(agent());
    expect(html).toContain('aria-label="Delete"');
  });
});
