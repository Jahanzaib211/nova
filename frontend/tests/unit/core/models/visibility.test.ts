import { beforeEach, describe, expect, test, vi } from "vitest";

import type { ModelVisibilityState } from "@/core/models/visibility";

/**
 * Minimal `window.localStorage` stand-in. The unit suite runs in node (no
 * jsdom), and the store only needs get/set/remove plus a `storage` listener to
 * behave like a browser tab, so this keeps the test honest without a DOM.
 */
class MemoryStorage {
  private data = new Map<string, string>();

  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }

  removeItem(key: string): void {
    this.data.delete(key);
  }

  clear(): void {
    this.data.clear();
  }

  key(index: number): string | null {
    return [...this.data.keys()][index] ?? null;
  }

  get length(): number {
    return this.data.size;
  }
}

const loadVisibility = () => import("@/core/models/visibility");

type VisibilityModule = Awaited<ReturnType<typeof loadVisibility>>;

let visibility: VisibilityModule;
let storage: MemoryStorage;
let emitStorage: (key: string | null) => void;

function installWindow(): void {
  storage = new MemoryStorage();
  const listeners = new Set<(event: Record<string, unknown>) => void>();
  const fakeWindow = {
    localStorage: storage,
    addEventListener: (type: string, listener: (e: never) => void) => {
      if (type === "storage") {
        listeners.add(listener as (e: Record<string, unknown>) => void);
      }
    },
    removeEventListener: () => undefined,
  };
  (globalThis as unknown as { window: unknown }).window = fakeWindow;
  emitStorage = (key) => {
    for (const listener of listeners) {
      listener({ key, storageArea: storage });
    }
  };
}

beforeEach(async () => {
  installWindow();
  vi.resetModules();
  visibility = await loadVisibility();
});

const model = (name: string, hidden?: boolean) => ({ name, hidden });

const state = (
  overrides: Record<string, boolean> = {},
  showHidden = false,
): ModelVisibilityState => ({ overrides, showHidden });

describe("isModelVisible", () => {
  test("follows the server flag when there is no override", () => {
    expect(visibility.isModelVisible(model("visible"), state())).toBe(true);
    expect(visibility.isModelVisible(model("hidden", true), state())).toBe(
      false,
    );
  });

  test("an override wins in both directions", () => {
    expect(
      visibility.isModelVisible(model("hidden", true), state({ hidden: true })),
    ).toBe(true);
    expect(
      visibility.isModelVisible(model("visible"), state({ visible: false })),
    ).toBe(false);
  });
});

describe("offeredModels", () => {
  const models = [model("a"), model("b", true), model("c", true)];

  test("hides server-hidden models by default", () => {
    expect(
      visibility.offeredModels(models, state()).map((m) => m.name),
    ).toEqual(["a"]);
  });

  test("show-hidden reveals everything without touching overrides", () => {
    expect(
      visibility.offeredModels(models, state({}, true)).map((m) => m.name),
    ).toEqual(["a", "b", "c"]);
  });

  test("surfaces a model the user made visible", () => {
    expect(
      visibility.offeredModels(models, state({ b: true })).map((m) => m.name),
    ).toEqual(["a", "b"]);
  });

  test("keeps the current selection visible even when hidden", () => {
    expect(
      visibility.offeredModels(models, state(), "c").map((m) => m.name),
    ).toEqual(["a", "c"]);
  });
});

describe("visibility store", () => {
  test("persists overrides and the show-hidden flag", () => {
    visibility.setModelVisible("minimax-m2.7", true);
    visibility.setShowHiddenModels(true);

    expect(
      JSON.parse(storage.getItem(visibility.MODEL_VISIBILITY_KEY) ?? "{}"),
    ).toEqual({ overrides: { "minimax-m2.7": true }, showHidden: true });
    expect(visibility.getModelVisibilitySnapshot()).toEqual({
      overrides: { "minimax-m2.7": true },
      showHidden: true,
    });
  });

  test("clearing an override follows the server flag again", () => {
    visibility.setModelVisible("minimax-m2.7", true);
    visibility.clearModelVisibility("minimax-m2.7");

    expect(visibility.getModelVisibilitySnapshot().overrides).toEqual({});
    expect(visibility.isModelVisible(model("minimax-m2.7", true))).toBe(false);
  });

  test("reset drops every preference", () => {
    visibility.setModelVisible("a", false);
    visibility.setShowHiddenModels(true);
    visibility.resetModelVisibility();

    expect(visibility.getModelVisibilitySnapshot()).toEqual({
      overrides: {},
      showHidden: false,
    });
  });

  test("a corrupt stored value is ignored, not thrown", async () => {
    storage.setItem(visibility.MODEL_VISIBILITY_KEY, "{not json");
    vi.resetModules();
    const fresh = await loadVisibility();

    expect(fresh.getModelVisibilitySnapshot()).toEqual({
      overrides: {},
      showHidden: false,
    });
  });

  test("drops non-boolean overrides when reading storage", async () => {
    storage.setItem(
      visibility.MODEL_VISIBILITY_KEY,
      JSON.stringify({ overrides: { a: "yes", b: false }, showHidden: "1" }),
    );
    vi.resetModules();
    const fresh = await loadVisibility();

    expect(fresh.getModelVisibilitySnapshot()).toEqual({
      overrides: { b: false },
      showHidden: false,
    });
  });

  test("another tab's change is picked up through the storage event", () => {
    const seen: ModelVisibilityState[] = [];
    const unsubscribe = visibility.subscribeModelVisibility(() => {
      seen.push(visibility.getModelVisibilitySnapshot());
    });

    storage.setItem(
      visibility.MODEL_VISIBILITY_KEY,
      JSON.stringify({ overrides: { a: true }, showHidden: true }),
    );
    emitStorage(visibility.MODEL_VISIBILITY_KEY);
    unsubscribe();

    expect(seen.at(-1)).toEqual({ overrides: { a: true }, showHidden: true });
  });

  test("unrelated storage keys do not notify", () => {
    const listener = vi.fn();
    const unsubscribe = visibility.subscribeModelVisibility(listener);
    emitStorage("deerflow.local-settings");
    unsubscribe();

    expect(listener).not.toHaveBeenCalled();
  });
});
