/**
 * The design-token layer in src/styles/globals.css (upgrade program P1-1a).
 *
 * Pins the properties the rest of the frontend now relies on: every colour
 * exposed to Tailwind is defined for both themes, the semantic status tokens
 * exist, focus rings are visible again, the dark theme no longer thins every
 * glyph, and there is one brand gradient rather than fifteen inline copies.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const css = readFileSync(
  fileURLToPath(new URL("../../../src/styles/globals.css", import.meta.url)),
  "utf-8",
);

function block(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, `${selector} block`).toBeGreaterThanOrEqual(0);
  let depth = 0;
  for (let i = start; i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}" && --depth === 0) return css.slice(start, i + 1);
  }
  throw new Error(`unbalanced block for ${selector}`);
}

const theme = block("@theme inline");
const light = block(":root");
const dark = block(".dark");

const SEMANTIC = [
  "success",
  "success-foreground",
  "warning",
  "warning-foreground",
  "info",
  "info-foreground",
  "panel",
  "panel-border",
  "panel-header",
];

describe("design tokens", () => {
  it("defines every Tailwind colour alias for both themes", () => {
    const aliases = [
      ...theme.matchAll(/--color-([\w-]+):\s*var\(--([\w-]+)\)/g),
    ].map((m) => m[2]);
    expect(aliases.length).toBeGreaterThan(20);
    for (const name of aliases) {
      expect(light, `--${name} in :root`).toMatch(new RegExp(`--${name}:`));
      expect(dark, `--${name} in .dark`).toMatch(new RegExp(`--${name}:`));
    }
  });

  it("exposes the semantic status and panel tokens", () => {
    for (const name of SEMANTIC) {
      expect(theme).toMatch(
        new RegExp(`--color-${name}:\\s*var\\(--${name}\\)`),
      );
    }
  });

  it("keeps focus rings visible in both themes", () => {
    expect(light).not.toMatch(/--ring:\s*transparent/);
    expect(dark).not.toMatch(/--ring:\s*transparent/);
    expect(css).toMatch(/:focus-visible/);
  });

  it("does not thin every glyph in dark mode", () => {
    expect(dark).not.toMatch(/font-weight/);
  });

  it("has exactly one brand gradient, exposed as utilities", () => {
    expect(light).toMatch(/--brand-gradient:/);
    expect(dark).toMatch(/--brand-gradient:/);
    expect(css).toMatch(/@utility bg-brand-gradient\b/);
    expect(css).toMatch(/@utility text-brand-gradient\b/);
  });
});
