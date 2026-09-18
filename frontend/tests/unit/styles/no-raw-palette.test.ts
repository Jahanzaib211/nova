/**
 * Ratchet against raw Tailwind palette classes in workspace components.
 *
 * Status colours belong to the semantic tokens (`text-success`,
 * `text-warning`, `text-info`, `text-destructive`); a raw `text-emerald-400`
 * cannot follow the theme, cannot be tuned in one place, and made the
 * baseline audit count ~100 one-off colours. The allowlist below is the
 * count at the time of writing per file — it may only go down. A file not
 * listed must have zero.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const ROOT = fileURLToPath(
  new URL("../../../src/components/workspace", import.meta.url),
);
const RAW =
  /\b(?:text|bg|border|from|to|via|ring|fill|stroke)-(?:emerald|violet|cyan|amber|rose|sky|red|orange|green|blue|purple|yellow|teal|indigo|pink|lime|fuchsia|slate|zinc|neutral|stone|gray)-\d{2,3}\b/g;

/** Remaining raw palette classes per file. Lower the number when you fix a file. */
const ALLOWLIST: Record<string, number> = {
  "settings/appearance-settings-page.tsx": 5,
  "settings/voice-lab.tsx": 7,
  "settings/voice-settings-page.tsx": 9,
  "voice-button.tsx": 20,
  "voice-orb.tsx": 9,
};

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (p.endsWith(".tsx") || p.endsWith(".ts")) out.push(p);
  }
  return out;
}

describe("raw palette ratchet", () => {
  it("never grows", () => {
    const over: string[] = [];
    for (const file of walk(ROOT)) {
      const rel = file.slice(ROOT.length + 1);
      const count = (readFileSync(file, "utf-8").match(RAW) ?? []).length;
      const allowed = ALLOWLIST[rel] ?? 0;
      if (count > allowed) over.push(`${rel}: ${count} (allowed ${allowed})`);
    }
    expect(over, "raw palette classes above the allowlist").toEqual([]);
  });
});
