/**
 * The merged Telemetry tab must gate its segments, not just its tab.
 *
 * Every tab in the Agent's Computer panel stays mounted and is only hidden with
 * CSS, so a panel that polls has to be told when it is off-screen or it fetches
 * forever — that invariant is documented in frontend/CLAUDE.md and was learned
 * by leaving an EventSource open for the life of a thread.
 *
 * Merging Activity and Audit into one tab quietly widens that hazard: a hidden
 * *segment* is exactly as off-screen as a hidden *tab*, and AuditPanel polls
 * `/api/sandbox/audit`. Forwarding a bare `active` would have reintroduced the
 * bug the gating exists to prevent, while looking completely correct.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test, vi } from "vitest";

const activeFlags: { activity: boolean[]; audit: boolean[] } = {
  activity: [],
  audit: [],
};

vi.mock("@/components/workspace/agent-computer/activity-tab", () => ({
  ActivityPanel: ({ active }: { active?: boolean }) => {
    activeFlags.activity.push(active ?? true);
    return <div data-testid="activity" />;
  },
}));

vi.mock("@/components/workspace/agent-computer/audit-tab", () => ({
  AuditPanel: ({ active }: { active?: boolean }) => {
    activeFlags.audit.push(active ?? true);
    return <div data-testid="audit" />;
  },
}));

const { TelemetryPanel } = await import(
  "@/components/workspace/agent-computer/telemetry-tab"
);
const { I18nProvider } = await import("@/core/i18n/context");

function render(active: boolean) {
  activeFlags.activity = [];
  activeFlags.audit = [];
  return renderToStaticMarkup(
    <I18nProvider initialLocale="en-US">
      <TelemetryPanel threadId="t1" events={[]} active={active} />
    </I18nProvider>,
  );
}

describe("TelemetryPanel segment gating", () => {
  test("the hidden segment is told it is inactive", () => {
    render(true);
    // Timeline is the default segment, so Ledger is off-screen and must not poll.
    expect(activeFlags.activity).toEqual([true]);
    expect(activeFlags.audit).toEqual([false]);
  });

  test("an inactive tab deactivates both segments", () => {
    render(false);
    expect(activeFlags.activity).toEqual([false]);
    expect(activeFlags.audit).toEqual([false]);
  });

  test("both segments stay mounted so switching keeps their state", () => {
    const html = render(true);
    expect(html).toContain('data-segment="timeline"');
    expect(html).toContain('data-segment="ledger"');
  });

  test("the off-screen segment is hidden with CSS, not unmounted", () => {
    const html = render(true);
    // Unmounting Ledger would drop the audit list and refetch on every switch.
    // Attribute order is React's to choose, so match the whole tag rather than
    // assuming `data-segment` precedes `class`.
    const ledgerTag = /<div[^>]*data-segment="ledger"[^>]*>/.exec(html)?.[0] ?? "";
    expect(ledgerTag).not.toBe("");
    expect(ledgerTag).toContain("hidden");
  });
});
