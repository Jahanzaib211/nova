/**
 * The runtime bar's status badge (2026-10-31).
 *
 * It read a bare "Healthy" whenever no browser circuit was open, while the
 * Browser tab directly below it reported "Self-test found issues" for the same
 * chat. Closed circuits only mean the browser is reachable; the badge now says
 * so, and turns amber when this chat's own self-test failed.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test, vi } from "vitest";

import { StatusLine } from "@/components/workspace/agent-computer/status-line";
import { deriveStatusVisuals } from "@/components/workspace/runtime-capabilities-bar";
import { I18nProvider } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";

vi.mock("motion/react", () => ({
  AnimatePresence: ({ children }: { children: React.ReactNode }) => children,
  motion: {
    span: ({
      children,
      className,
    }: {
      children: React.ReactNode;
      className?: string;
    }) => <span className={className}>{children}</span>,
  },
}));

describe("deriveStatusVisuals", () => {
  test("no open circuits and a passing self-test is browser-healthy", () => {
    const { label } = deriveStatusVisuals(0, false, enUS);
    expect(label.state).toBe("healthy");
    expect(label.text).toBe("Browser healthy");
  });

  test("a failed self-test is not reported as healthy", () => {
    const { label, tone } = deriveStatusVisuals(0, true, enUS);
    expect(label.state).toBe("self-test-issues");
    expect(tone.dot).toBe("bg-warning");
  });

  test("open circuits outrank the self-test signal", () => {
    expect(deriveStatusVisuals(1, true, enUS).label.state).toBe("degraded");
    expect(deriveStatusVisuals(5, false, enUS).label.state).toBe("critical");
  });
});

describe("StatusLine", () => {
  test("names its subject -- every status string is a predicate", () => {
    const html = renderToStaticMarkup(
      <I18nProvider initialLocale="en-US">
        <StatusLine tool="task" isLoading filePath={null} />
      </I18nProvider>,
    );
    const text = html.replace(/<[^>]+>/g, "");
    expect(text).toContain("Nova is delegating to subagent");
  });
});
