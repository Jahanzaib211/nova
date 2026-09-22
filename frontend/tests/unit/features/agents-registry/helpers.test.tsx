import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/core/i18n/context";
import type { RegistryAgent } from "@/features/agents-registry/api";
import {
  AgentsRegistryList,
  groupByKind,
} from "@/features/agents-registry/components/agents-registry-list";

const state: { data?: unknown; isLoading: boolean; isError: boolean } = {
  isLoading: false,
  isError: false,
};
vi.mock("@/features/agents-registry/hooks", () => ({
  useAgentsRegistry: () => state,
}));

const a = (over: Partial<RegistryAgent>): RegistryAgent => ({
  id: "x",
  kind: "subagent",
  name: "x",
  description: "",
  model: null,
  runner: "gateway",
  async_capable: true,
  queued: 0,
  running: 0,
  ...over,
});

const render = () =>
  renderToStaticMarkup(
    <I18nProvider initialLocale="en-US">
      <AgentsRegistryList />
    </I18nProvider>,
  );

describe("agents registry", () => {
  it("groups by kind in a fixed order and drops empty groups", () => {
    const groups = groupByKind([
      a({ id: "acp:c", kind: "acp" }),
      a({ id: "lead", kind: "lead" }),
      a({ id: "s:g", kind: "subagent" }),
    ]);
    expect(groups.map((g) => g.kind)).toEqual(["lead", "subagent", "acp"]);
  });

  it("renders honest states: loading, error, and counts only when active", () => {
    state.isLoading = true;
    expect(render()).toContain('aria-busy="true"');
    state.isLoading = false;
    state.isError = true;
    expect(render()).toContain("Could not load the agent registry");
    state.isError = false;
    state.data = {
      async_enabled: true,
      agents: [
        a({ id: "lead", kind: "lead", name: "Nova" }),
        a({
          id: "s:g",
          name: "general-purpose",
          running: 1,
          queued: 2,
          runner: "jobs",
        }),
      ],
    };
    const html = render();
    expect(html).toContain('data-async="true"');
    expect(html).toContain("1 running · 2 queued");
    expect(html).toContain("1 running, 2 queued");
    // The idle lead agent shows no counts badge at all.
    expect(html.match(/data-testid="registry-counts"/g)?.length).toBe(1);
  });
});
