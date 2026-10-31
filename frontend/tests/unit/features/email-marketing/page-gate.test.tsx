import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { enUS } from "@/core/i18n";
import { I18nProvider } from "@/core/i18n/context";
import { EmailPage } from "@/features/email-marketing/pages/email-page";

/**
 * The page-level half of the gate: with the server flag off, /workspace/email
 * must not render the campaigns/lists/contacts/templates/suppressions UI at
 * all — that API is not mounted on such a deployment, so every panel would
 * only ever be an error. The capabilities response is seeded into the query
 * cache so the render is deterministic (no network, no timers).
 */
function renderPage(features: Record<string, boolean>) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(["runtime", "capabilities", null], {
    features,
    skills: [],
    tools: [],
    hooks: [],
    subagents: [],
    circuits: [],
    server: {},
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <I18nProvider initialLocale="en-US">
        <EmailPage />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("/workspace/email is gated on the email_marketing flag", () => {
  it("says the feature is off instead of rendering the email UI", () => {
    const html = renderPage({ email_marketing: false });
    expect(html).toContain("email-disabled");
    expect(html).toContain(enUS.features.email.disabledHint);
    // None of the panels are mounted.
    expect(html).not.toContain("em-campaigns");
    expect(html).not.toContain("em-lists");
    expect(html).not.toContain("em-contacts");
    expect(html).not.toContain("em-templates");
    expect(html).not.toContain("em-suppressions");
    expect(html).not.toContain(enUS.features.email.tabs.campaigns);
  });

  it("renders the email UI when the server says the feature is on", () => {
    const html = renderPage({ email_marketing: true });
    expect(html).not.toContain("email-disabled");
    expect(html).toContain("em-campaigns");
    expect(html).toContain(enUS.features.email.tabs.campaigns);
    expect(html).toContain(enUS.features.email.tabs.suppressions);
  });

  it("keeps the page title in both states", () => {
    for (const on of [true, false]) {
      const html = renderPage({ email_marketing: on });
      expect(html).toContain("email-page");
      expect(html).toContain(enUS.features.email.title);
    }
  });
});
