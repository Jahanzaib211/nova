import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PasswordInput } from "@/components/password-input";
import { I18nProvider } from "@/core/i18n/context";

const render = (locale: "en-US" | "zh-CN" = "en-US") =>
  renderToStaticMarkup(
    <I18nProvider initialLocale={locale}>
      <PasswordInput
        id="password"
        name="password"
        placeholder="•••"
        autoComplete="current-password"
        required
        minLength={8}
      />
    </I18nProvider>,
  );

describe("PasswordInput", () => {
  it("starts masked with a show toggle that is not pressed", () => {
    const html = render();
    expect(html).toContain('type="password"');
    expect(html).not.toContain('type="text"');
    expect(html).toContain('aria-label="Show password"');
    expect(html).toContain('aria-pressed="false"');
  });

  it("forwards input props to the field, not the wrapper", () => {
    const html = render();
    expect(html).toMatch(/<input[^>]*id="password"/);
    expect(html).toMatch(/<input[^>]*autoComplete="current-password"/i);
    expect(html).toMatch(/<input[^>]*minLength="8"/i);
    expect(html).toMatch(/<input[^>]*required/);
  });

  it("toggle is a plain button so it never submits the form", () => {
    expect(render()).toMatch(/<button[^>]*type="button"/);
  });

  it("labels the toggle in the active locale", () => {
    expect(render("zh-CN")).toContain('aria-label="显示密码"');
  });
});
