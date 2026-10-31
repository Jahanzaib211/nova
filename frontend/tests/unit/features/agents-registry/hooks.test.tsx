/**
 * The agent registry query.
 *
 * The registry and a single custom agent are two different resources, and
 * custom agent names match /^[A-Za-z0-9-]+$/ — so "registry" is a legal
 * agent name. While both queries used the key `["agents", "registry"]`
 * (registry) and `["agents", <name>]` (one agent), an agent named
 * "registry" shared one cache entry with the registry: the two queries
 * fought over the entry's queryFn and the registry could end up serving an
 * agent payload (or the agent serving the registry payload).
 */
import {
  QueryClient,
  QueryClientProvider,
  useQueryClient,
} from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { useAgent } from "@/core/agents/hooks";
import { useAgentsRegistry } from "@/features/agents-registry/hooks";

vi.mock("@/features/agents-registry/api", () => ({
  fetchAgentsRegistry: vi.fn(async () => ({
    async_enabled: true,
    agents: [
      {
        id: "lead",
        kind: "lead",
        name: "Nova",
        description: "",
        model: null,
        runner: "gateway",
        async_capable: false,
        queued: 0,
        running: 0,
      },
    ],
  })),
}));

vi.mock("@/core/agents/api", () => ({
  AgentsApiDisabledError: class AgentsApiDisabledError extends Error {},
  AgentNameCheckError: class AgentNameCheckError extends Error {},
  listAgents: vi.fn(async () => []),
  getAgent: vi.fn(async (name: string) => ({
    name,
    description: "a custom agent",
    model: null,
    tool_groups: null,
    skills: null,
  })),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  deleteAgent: vi.fn(),
  checkAgentName: vi.fn(),
}));

function Probe() {
  useAgentsRegistry();
  useAgent("registry");
  const client = useQueryClient();
  const keys = client
    .getQueryCache()
    .getAll()
    .map((q) => (q.queryKey as unknown[]).join("|"));
  return <pre>{keys.join("\n")}</pre>;
}

describe("agents registry query key", () => {
  it("does not share a cache entry with a custom agent named 'registry'", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, enabled: false } },
    });
    const html = renderToStaticMarkup(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>,
    );
    const keys = html
      .replace(/^<pre>|<\/pre>$/g, "")
      .split("\n")
      .filter(Boolean);
    expect(keys).toHaveLength(2);
    // The registry entry and the single-agent entry are distinct.
    expect(keys[0]).not.toEqual(keys[1]);
    expect(keys).toContain("agents|registry|list");
    expect(keys).toContain("agents|registry");
  });

  it("stays under the 'agents' prefix so custom-agent edits invalidate it", () => {
    // useCreateAgent / useUpdateAgent / useDeleteAgent invalidate
    // ["agents"], and the registry lists custom agents — it has to be
    // invalidated by the same call.
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, enabled: false } },
    });
    renderToStaticMarkup(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>,
    );
    const registryKey = client
      .getQueryCache()
      .getAll()
      .map((q) => q.queryKey as unknown[])
      .find((k) => k[1] === "registry" && k[2] === "list");
    expect(registryKey).toBeDefined();
    expect(registryKey?.slice(0, 1)).toEqual(["agents"]);
  });
});
