/**
 * The Agents gallery (/workspace/agents).
 *
 * The error state's "Try again" button used to call `router.refresh()`.
 * The agent list is fetched in the browser (react-query), and a router
 * refresh re-renders the server components while leaving the client cache
 * — and its error state — exactly as it was: the button looked like it
 * worked and nothing changed. The retry has to refetch the query.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AgentGallery } from "@/components/workspace/agents/agent-gallery";
import { I18nProvider } from "@/core/i18n/context";

const { buttons, agentsState, router } = vi.hoisted(() => ({
  buttons: [] as Array<{ text: string; onClick?: () => void }>,
  agentsState: {
    agents: [] as unknown[],
    isLoading: false,
    isFetching: false,
    error: null as unknown,
    isDisabled: false,
    refetch: vi.fn(),
  },
  router: { push: vi.fn(), refresh: vi.fn(), replace: vi.fn() },
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    onClick,
  }: {
    children?: React.ReactNode;
    onClick?: () => void;
  }) => {
    buttons.push({
      text: typeof children === "string" ? children : "",
      onClick,
    });
    return (
      <button type="button" onClick={onClick}>
        {children}
      </button>
    );
  },
}));

vi.mock("@/core/agents", () => ({
  useAgents: () => agentsState,
}));

vi.mock("@/features/agents-registry/hooks", () => ({
  useAgentsRegistry: () => ({
    data: { async_enabled: false, agents: [] },
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("@/components/workspace/agents/agent-card", () => ({
  AgentCard: () => null,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

const render = () =>
  renderToStaticMarkup(
    <I18nProvider initialLocale="en-US">
      <AgentGallery />
    </I18nProvider>,
  );

describe("agents gallery error state", () => {
  it("refetches the agent list when the user asks to try again", () => {
    buttons.length = 0;
    agentsState.refetch.mockClear();
    router.refresh.mockClear();
    agentsState.error = new Error("gateway down");

    const html = render();
    expect(html).toContain("Couldn&#x27;t load agents");

    const retry = buttons.find((b) => b.text.includes("Try again"));
    expect(retry).toBeDefined();
    retry?.onClick?.();

    expect(agentsState.refetch).toHaveBeenCalledTimes(1);
    // A router refresh would not touch the failed client-side query.
    expect(router.refresh).not.toHaveBeenCalled();
    agentsState.error = null;
  });
});
