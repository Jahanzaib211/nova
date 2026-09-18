import { resolve } from "path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  test: {
    // Accept both .ts (pure-logic) and .tsx (component) tests.
    include: ["tests/unit/**/*.test.ts", "tests/unit/**/*.test.tsx"],
    // `streamdown` imports katex's stylesheet from its ESM entry; when Node
    // loads it natively that is "Unknown file extension .css". Inline it so
    // vitest transforms the import (to nothing) like a bundler would — this
    // lets tests import page components that render markdown.
    server: { deps: { inline: ["streamdown"] } },
    css: false,
  },
});
