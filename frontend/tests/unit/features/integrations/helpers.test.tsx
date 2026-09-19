import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { enUS } from "@/core/i18n";
import { I18nProvider } from "@/core/i18n/context";
import {
  IntegrationCard,
  relativeCheckedAt,
} from "@/features/integrations/components/integration-card";
import {
  KIND_GROUPS,
  groupIntegrations,
  summarize,
  toneFor,
} from "@/features/integrations/status";
import {
  INTEGRATION_KINDS,
  INTEGRATION_STATUSES,
  type IntegrationHealthItem,
} from "@/features/integrations/types";

const item = (
  id: string,
  over: Partial<IntegrationHealthItem> = {},
): IntegrationHealthItem => ({
  id,
  kind: "llm_gateway",
  display_name: id,
  endpoint: `http://${id}`,
  status: "healthy",
  latency_ms: 12.6,
  checked_at: "2026-09-19T12:00:00Z",
  detail: "4 models",
  capabilities: ["a", "b"],
  ...over,
});

describe("integration status presentation", () => {
  it("assigns every contract status a tone", () => {
    for (const s of INTEGRATION_STATUSES) expect(toneFor(s)).toBeTruthy();
    expect(toneFor("healthy")).toBe("success");
    expect(toneFor("degraded")).toBe("warning");
    expect(toneFor("down")).toBe("destructive");
    expect(toneFor("disabled")).toBe("muted");
  });

  it("places every contract kind in exactly one group", () => {
    for (const kind of INTEGRATION_KINDS) {
      const hits = KIND_GROUPS.filter((g) => g.kinds.includes(kind));
      expect(hits, kind).toHaveLength(1);
    }
  });

  it("groups in display order and drops empty groups", () => {
    const groups = groupIntegrations([
      item("mcp:x", { kind: "mcp_server" }),
      item("ollama"),
      item("mailcow", { kind: "mail" }),
    ]);
    expect(groups.map((g) => g.id)).toEqual([
      "models",
      "business",
      "extensions",
    ]);
  });

  it("summarises counts per status", () => {
    const counts = summarize([
      item("a"),
      item("b", { status: "down" }),
      item("c", { status: "down" }),
    ]);
    expect(counts).toMatchObject({ healthy: 1, down: 2, degraded: 0 });
  });
});

describe("relativeCheckedAt", () => {
  const s = enUS.features.integrations;
  const now = Date.parse("2026-09-19T12:00:00Z");
  it("never claims a check that did not happen", () => {
    expect(relativeCheckedAt(null, s, now)).toBe("never checked");
    expect(relativeCheckedAt("2026-09-19T11:59:58Z", s, now)).toBe("just now");
    expect(relativeCheckedAt("2026-09-19T11:59:20Z", s, now)).toBe("40s ago");
    expect(relativeCheckedAt("2026-09-19T11:50:00Z", s, now)).toBe(
      "10 min ago",
    );
  });
});

const noop = (_id: string) => undefined;
const render = (node: React.ReactNode) =>
  renderToStaticMarkup(
    <I18nProvider initialLocale="en-US">{node}</I18nProvider>,
  );

describe("IntegrationCard", () => {
  it("renders status, endpoint, detail, capabilities and latency", () => {
    const html = render(
      <IntegrationCard
        item={item("ollama", {
          capabilities: ["q", "w", "e", "r", "t", "y", "u"],
        })}
      />,
    );
    expect(html).toContain('data-status="healthy"');
    expect(html).toContain("http://ollama");
    expect(html).toContain("4 models");
    expect(html).toContain("13 ms");
    // Six chips and a "+1" overflow, never a wall of model names.
    expect(html).toContain("+1");
  });

  it("offers Probe only for probeable services, not adapters", () => {
    const withEndpoint = render(
      <IntegrationCard item={item("ollama")} onProbe={noop} />,
    );
    const adapter = render(
      <IntegrationCard
        item={item("skill:pdf", { kind: "skill", endpoint: null })}
        onProbe={noop}
      />,
    );
    expect(withEndpoint).toContain("Probe ollama");
    expect(adapter).not.toContain("Probe skill:pdf");
  });
});
