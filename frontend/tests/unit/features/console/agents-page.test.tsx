/**
 * Settings › Agents › Runtimes — the console page that lists the runtimes a
 * chat turn can use.
 *
 * The page is gated on the server-declared `runtimes` flag. Two things are
 * pinned here:
 *
 *  1. It must not claim runtimes are switched off before the server has
 *     answered. Every feature flag reads as off until
 *     `/api/runtime/capabilities` responds, so the "runtimes.enabled is
 *     false in config.yaml" hint shown during that window is a statement
 *     the next tick contradicts.
 *  2. A runtime entry without an `accounts` key must not take the page
 *     down — the capability contract types a runtime as
 *     `Record<string, unknown>` and the page casts it.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/core/i18n/context";
import {
  AgentsPage,
  runtimeAccounts,
} from "@/features/console/pages/agents-page";

const { capabilityState, flagsState } = vi.hoisted(() => ({
  capabilityState: {
    data: undefined as unknown,
    isLoading: false,
    isError: false,
  },
  flagsState: {
    flags: { runtimes: false } as { runtimes: boolean },
    isLoading: false,
  },
}));

vi.mock("@/core/runtime/feature-flags", () => ({
  useFeatureFlags: () => flagsState.flags,
}));

vi.mock("@/core/runtime/hooks", () => ({
  useCapabilities: () => ({
    capabilities: null,
    isLoading: flagsState.isLoading,
  }),
}));

vi.mock("@/core/capabilities", () => ({
  useCapability: () => capabilityState,
  useCapabilityMutation: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock("@/components/workspace/settings/native-agent-settings", () => ({
  NativeAgentSettings: () => null,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children?: React.ReactNode;
    href?: string;
  }) => <a href={href}>{children}</a>,
}));

const render = (locale: "en-US" | "zh-CN" = "en-US") =>
  renderToStaticMarkup(
    <I18nProvider initialLocale={locale}>
      <AgentsPage />
    </I18nProvider>,
  );

describe("settings › agents › runtimes", () => {
  it("does not claim runtimes are disabled while the flags are loading", () => {
    flagsState.isLoading = true;
    flagsState.flags = { runtimes: false };
    const html = render();
    expect(html).toContain("Loading");
    expect(html).not.toContain("runtimes.enabled is false");
    flagsState.isLoading = false;
  });

  it("says runtimes are off once the server has said so", () => {
    flagsState.isLoading = false;
    flagsState.flags = { runtimes: false };
    const html = render();
    expect(html).toContain("runtimes.enabled is false");
    expect(html).not.toContain("Loading");
  });

  it("links to the agent registry instead of repeating it", () => {
    flagsState.isLoading = false;
    flagsState.flags = { runtimes: false };
    const html = render();
    expect(html).toContain('href="/workspace/agents"');
  });
});

describe("runtimeAccounts", () => {
  it("reads the accounts of a runtime entry", () => {
    const accounts = [
      { id: "a", label: "Claude login", available: true, detail: "ok" },
    ];
    expect(runtimeAccounts({ accounts })).toEqual(accounts);
  });

  it("survives an entry with no accounts key", () => {
    // The capability contract types a runtime as Record<string, unknown>,
    // so the page casts it before reading. A runtime without `accounts`
    // would otherwise crash the whole settings section on `.map`.
    expect(runtimeAccounts({})).toEqual([]);
    expect(runtimeAccounts({ accounts: null })).toEqual([]);
    expect(runtimeAccounts({ accounts: "nope" })).toEqual([]);
  });
});
