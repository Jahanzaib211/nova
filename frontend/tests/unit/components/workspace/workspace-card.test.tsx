/**
 * C10 Batch 1 — workspace snapshot client + WorkspaceCard.
 *
 * Pins the graceful-degradation contract:
 * - 403 (workspace.intelligence_enabled off) -> "disabled", card renders nothing
 * - 404 (never indexed) -> "unindexed", card offers a one-click index
 * - 200 -> snapshot summary, card shows language/kind/counts/projects
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, test, vi } from "vitest";

import { WorkspaceCard } from "@/components/workspace/agent-computer/workspace-card";
import { I18nProvider } from "@/core/i18n/context";
import {
  fetchWorkspaceSnapshot,
  WorkspaceDisabledError,
  WorkspaceUnindexedError,
  type WorkspaceSnapshotSummary,
} from "@/core/workspace/api";
import type { WorkspaceSnapshotState } from "@/core/workspace/hooks";

const SNAPSHOT: WorkspaceSnapshotSummary = {
  thread_workspace: "/w",
  repo_kind: "backend",
  primary_language: "python",
  is_monorepo: true,
  project_count: 2,
  symbol_count: 1234,
  command_count: 7,
  node_count: 10,
  edge_count: 9,
  traversal_count: 100,
  duration_ms: 42,
  projects: [
    {
      project_id: "p1",
      name: "backend",
      kind: "python",
      root_path: "/w/backend",
    },
    {
      project_id: "p2",
      name: "frontend",
      kind: "node",
      root_path: "/w/frontend",
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("workspace api client", () => {
  test("403 raises WorkspaceDisabledError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("", { status: 403 })),
    );
    await expect(fetchWorkspaceSnapshot("t1")).rejects.toBeInstanceOf(
      WorkspaceDisabledError,
    );
  });

  test("404 raises WorkspaceUnindexedError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("", { status: 404 })),
    );
    await expect(fetchWorkspaceSnapshot("t1")).rejects.toBeInstanceOf(
      WorkspaceUnindexedError,
    );
  });

  test("200 returns the snapshot summary", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ snapshot: SNAPSHOT }), { status: 200 }),
        ),
    );
    const snapshot = await fetchWorkspaceSnapshot("t1");
    expect(snapshot.symbol_count).toBe(1234);
    expect(snapshot.projects).toHaveLength(2);
  });
});

function renderCard(state: WorkspaceSnapshotState): string {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, enabled: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <I18nProvider initialLocale="en-US">
        <WorkspaceCard state={state} />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("WorkspaceCard", () => {
  const noop = () => undefined;

  test("renders nothing when the kernel is disabled", () => {
    const html = renderCard({
      availability: "disabled",
      snapshot: null,
      isIndexing: false,
      refresh: noop,
    });
    expect(html).toBe("");
  });

  test("offers indexing when unindexed", () => {
    const html = renderCard({
      availability: "unindexed",
      snapshot: null,
      isIndexing: false,
      refresh: noop,
    });
    expect(html).toContain("Workspace");
    expect(html).toContain("Index");
  });

  test("shows language, kind, counts, and project chips with a snapshot", () => {
    const html = renderCard({
      availability: "available",
      snapshot: SNAPSHOT,
      isIndexing: false,
      refresh: noop,
    });
    expect(html).toContain("python");
    expect(html).toContain("backend");
    expect(html).toContain("1,234");
    expect(html).toContain("7");
    expect(html).toContain("monorepo");
    expect(html).toContain("frontend");
  });
});
