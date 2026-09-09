import { describe, expect, it } from "vitest";

/**
 * The runtime bar's skill rail renders `enabled.slice(0, MAX)` but computed its
 * "+N more" indicator from `skills.length` — the *installed* total. With 28
 * installed and 5 enabled it drew 5 pills and claimed "+20 more" while hiding
 * nothing. Invisible in the common 28/28 case, which is why it survived.
 *
 * Mirrors the shipped expression; the component itself needs a DOM harness,
 * and the defect was purely arithmetic.
 */
const MAX_VISIBLE_SKILLS = 8;

function overflowFor(skills: { enabled: boolean }[]): number {
  const enabled = skills.filter((s) => s.enabled);
  return enabled.length - MAX_VISIBLE_SKILLS;
}

const make = (total: number, enabled: number) =>
  Array.from({ length: total }, (_, i) => ({ enabled: i < enabled }));

describe("skill rail overflow", () => {
  it("hides nothing when few are enabled, however many are installed", () => {
    // The regression: 28 installed, 5 enabled — all 5 render, so no overflow.
    expect(overflowFor(make(28, 5))).toBeLessThanOrEqual(0);
  });

  it("counts only what the rail actually hides", () => {
    expect(overflowFor(make(28, 12))).toBe(4);
  });

  it("is zero exactly at the cap", () => {
    expect(overflowFor(make(28, MAX_VISIBLE_SKILLS))).toBe(0);
  });

  it("never reports overflow from the installed total", () => {
    // 28 - 8 = 20 was the old, wrong answer for every one of these.
    for (const enabled of [0, 1, 5, 8]) {
      expect(overflowFor(make(28, enabled))).not.toBe(28 - MAX_VISIBLE_SKILLS);
    }
  });
});
