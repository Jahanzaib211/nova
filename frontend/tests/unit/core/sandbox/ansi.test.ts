import { describe, expect, it } from "vitest";

import { parseAnsi, stripAnsi } from "@/core/sandbox/ansi";

const ESC = "\x1b";

/**
 * The backend keeps SGR and strips everything else (deerflow/utils/sanitize.py),
 * so this parser only has to turn colour into spans. It must also stay safe on
 * rows written before that sanitiser existed, which still carry cursor codes and
 * would otherwise render as visible garbage — 39 such lines exist in the live
 * logs today.
 */
describe("parseAnsi", () => {
  it("returns plain text untouched", () => {
    expect(parseAnsi("ready in 1.2s")).toEqual([
      { text: "ready in 1.2s", className: "" },
    ]);
  });

  it("colours an SGR run", () => {
    const segs = parseAnsi(`${ESC}[32mready${ESC}[0m`);
    expect(segs).toHaveLength(1);
    expect(segs[0]!.text).toBe("ready");
    expect(segs[0]!.className).toContain("text-emerald-400");
  });

  it("keeps unstyled text around a coloured run", () => {
    const segs = parseAnsi(`before ${ESC}[31merr${ESC}[0m after`);
    expect(segs.map((s) => s.text)).toEqual(["before ", "err", " after"]);
    expect(segs[0]!.className).toBe("");
    expect(segs[1]!.className).toContain("text-red-400");
    expect(segs[2]!.className).toBe("");
  });

  it("combines bold with colour", () => {
    const segs = parseAnsi(`${ESC}[1;31mfatal${ESC}[0m`);
    expect(segs[0]!.className).toContain("font-semibold");
    expect(segs[0]!.className).toContain("text-red-400");
  });

  it("resets on a bare ESC[m", () => {
    const segs = parseAnsi(`${ESC}[31mred${ESC}[mplain`);
    expect(segs[1]!.className).toBe("");
  });

  it("drops non-SGR escapes rather than showing them", () => {
    // The exact shape found in the live logs.
    expect(
      parseAnsi(`[dev] ${ESC}[?25h`)
        .map((s) => s.text)
        .join(""),
    ).toBe("[dev] ");
  });

  it("consumes 256-colour parameters instead of reading them as styles", () => {
    // 38;5;196 is "foreground = palette 196". A naive parser reads 5 as blink
    // and 196 as garbage; the colour index must not leak into the style.
    const segs = parseAnsi(`${ESC}[38;5;196mx${ESC}[0m`);
    expect(segs[0]!.text).toBe("x");
    expect(segs[0]!.className).not.toContain("font-semibold");
  });

  it("consumes truecolour parameters", () => {
    const segs = parseAnsi(`${ESC}[38;2;255;0;0mx${ESC}[0m`);
    expect(segs[0]!.text).toBe("x");
  });

  it("is reusable — the regex lastIndex cannot leak between calls", () => {
    const input = `${ESC}[32ma${ESC}[0m`;
    expect(parseAnsi(input)).toEqual(parseAnsi(input));
  });

  it("handles empty input", () => {
    expect(parseAnsi("")).toEqual([]);
  });
});

describe("stripAnsi", () => {
  it("removes colour as well as control sequences", () => {
    expect(stripAnsi(`${ESC}[32mready${ESC}[0m`)).toBe("ready");
    expect(stripAnsi(`[dev] ${ESC}[?25h`)).toBe("[dev] ");
  });

  it("leaves clean text alone", () => {
    expect(stripAnsi("plain")).toBe("plain");
  });
});
