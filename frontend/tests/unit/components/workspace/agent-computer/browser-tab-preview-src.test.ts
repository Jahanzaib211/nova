import { describe, expect, test } from "vitest";

import { buildPreviewSrc } from "@/components/workspace/agent-computer/browser-tab";

const BASE = "https://app.example";

describe("buildPreviewSrc", () => {
  test("returns the canonical preview URL when srcMode='preview'", () => {
    expect(
      buildPreviewSrc(
        BASE,
        "/api/sandbox/preview/t1",
        "/api/sandbox/absproxy/t1/3000/",
        "/",
        "preview",
      ),
    ).toBe("https://app.example/api/sandbox/preview/t1/");
  });

  test("returns the absproxy URL when srcMode='absproxy'", () => {
    expect(
      buildPreviewSrc(
        BASE,
        "/api/sandbox/preview/t1",
        "/api/sandbox/absproxy/t1/3000/",
        "/",
        "absproxy",
      ),
    ).toBe("https://app.example/api/sandbox/absproxy/t1/3000/");
  });

  test("preserves nested routes on the absproxy URL", () => {
    expect(
      buildPreviewSrc(
        BASE,
        "/api/sandbox/preview/t1",
        "/api/sandbox/absproxy/t1/3000/",
        "/_next/static/chunks/foo.js",
        "absproxy",
      ),
    ).toBe(
      "https://app.example/api/sandbox/absproxy/t1/3000/_next/static/chunks/foo.js",
    );
  });

  test("falls back to preview when absproxy is missing but srcMode='absproxy'", () => {
    expect(
      buildPreviewSrc(BASE, "/api/sandbox/preview/t1", null, "/", "absproxy"),
    ).toBe("https://app.example/api/sandbox/preview/t1/");
  });

  test("returns null when neither preview nor absproxy are reachable", () => {
    expect(buildPreviewSrc(BASE, null, null, "/", "preview")).toBeNull();
  });
});
