import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { enUS, zhCN } from "@/core/i18n";
import { I18nProvider } from "@/core/i18n/context";
import { FEATURE_MANIFESTS } from "@/features/registry";
import { voiceManifest } from "@/features/voice/manifest";
import { VoiceSettingsPage } from "@/features/voice/pages/voice-settings-page";

type Leaf = string | ((...args: never[]) => string);

/** Flatten a translation subtree to dotted keys with their leaf values. */
function leaves(node: unknown, prefix = ""): Array<[string, Leaf]> {
  if (typeof node === "string" || typeof node === "function")
    return [[prefix, node as Leaf]];
  if (node && typeof node === "object")
    return Object.entries(node as Record<string, unknown>).flatMap(([k, v]) =>
      leaves(v, prefix ? `${prefix}.${k}` : k),
    );
  return [];
}

const render = (locale: "en-US" | "zh-CN", node: React.ReactNode) =>
  renderToStaticMarkup(
    <I18nProvider initialLocale={locale}>{node}</I18nProvider>,
  );

describe("voice feature i18n", () => {
  const en = leaves(enUS.features.voice);
  const zh = new Map(leaves(zhCN.features.voice));

  it("has every key filled in both locales", () => {
    expect(en.length).toBeGreaterThan(50);
    for (const [key, value] of en) {
      expect(zh.has(key), `zh-CN missing ${key}`).toBe(true);
      const v = typeof value === "function" ? value("x" as never) : value;
      const z = zh.get(key)!;
      const zv = typeof z === "function" ? z("x" as never) : z;
      expect(v.trim(), `en-US ${key} empty`).not.toBe("");
      expect(zv.trim(), `zh-CN ${key} empty`).not.toBe("");
    }
  });

  it("actually translates: zh-CN differs from en-US for >90% of strings", () => {
    let same = 0;
    for (const [key, value] of en) {
      const z = zh.get(key)!;
      const a = typeof value === "function" ? value("x" as never) : value;
      const b = typeof z === "function" ? z("x" as never) : z;
      if (a === b) same += 1;
    }
    // "CPU" and "GPU (CUDA)" are legitimately identical.
    expect(same / en.length).toBeLessThan(0.1);
  });

  it("renders the page under zh-CN with translated chrome", () => {
    // Before config loads the page shows its title + tagline and a spinner.
    const html = render("zh-CN", <VoiceSettingsPage />);
    expect(html).toContain("语音");
    expect(html).toContain(zhCN.features.voice.tagline);
    expect(html).not.toContain("Talk to Nova");
  });

  it("is registered once, through its own manifest", () => {
    const voiceSpecs = FEATURE_MANIFESTS.flatMap(
      (m) => m.settings ?? [],
    ).filter((s) => s.id === "voice");
    expect(voiceSpecs).toHaveLength(1);
    expect(voiceSpecs[0]).toBe(voiceManifest.settings![0]);
  });
});
