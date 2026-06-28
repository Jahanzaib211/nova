import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test, vi } from "vitest";

vi.mock("streamdown", () => ({
  Streamdown: ({ children }: { children: string }) =>
    createElement("div", null, children),
}));

import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { I18nProvider } from "@/core/i18n/context";

test("ReasoningTrigger default message uses phrasing content", () => {
  const html = renderToStaticMarkup(
    <I18nProvider initialLocale="en-US">
      <Reasoning isStreaming={false} defaultOpen={false}>
        <ReasoningTrigger />
        <ReasoningContent>test</ReasoningContent>
      </Reasoning>
    </I18nProvider>,
  );

  expect(html).toContain("Thought for a few seconds");
  expect(html).not.toMatch(/<button\b[^>]*>[\s\S]*?<p\b/i);
});
