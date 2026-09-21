import { describe, expect, it } from "vitest";

import { languageFor } from "@/components/workspace/code-editor";

/**
 * CodeMirror 6 resolves `language` as a facet that keeps the FIRST value, so
 * passing every language extension at once does not mean "detect the right
 * one" — it means every document is highlighted as whichever came first.
 * The Agent's Computer editor was highlighting every file as CSS. Verified
 * directly: a Python document under `[css(), python()]` resolves to `css`.
 */
describe("languageFor", () => {
  it("returns at most one language, chosen by extension", () => {
    for (const name of ["a.py", "a.ts", "a.tsx", "a.json", "a.md", "a.css", "a.html"]) {
      const ext = languageFor(name);
      expect(ext, `${name} should resolve to a language`).not.toBeNull();
      expect(Array.isArray(ext)).toBe(false);
    }
  });

  it("distinguishes file types instead of collapsing them", () => {
    const py = languageFor("script.py");
    const css = languageFor("style.css");
    expect(py).not.toBeNull();
    expect(css).not.toBeNull();
    // Different extensions must not resolve to the same language.
    // (Compare identity, not JSON — these graphs are circular.)
    expect(py).not.toBe(css);
  });

  it("returns null for unknown or missing names, leaving text unhighlighted", () => {
    expect(languageFor(undefined)).toBeNull();
    expect(languageFor("Dockerfile")).toBeNull();
    expect(languageFor("binary.bin")).toBeNull();
  });

  it("is case-insensitive", () => {
    expect(languageFor("A.PY")).not.toBeNull();
    expect(languageFor("A.TSX")).not.toBeNull();
  });

  it("does not crash on odd names", () => {
    expect(() => languageFor("")).not.toThrow();
    expect(() => languageFor(".")).not.toThrow();
    expect(() => languageFor("no-extension")).not.toThrow();
  });
});
