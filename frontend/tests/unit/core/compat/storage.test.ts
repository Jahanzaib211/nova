import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  getMigratedItem,
  legacyStorageKey,
  removeMigratedItem,
} from "@/core/compat/storage";
import {
  getLocalSettings,
  getThreadModelName,
  LOCAL_SETTINGS_KEY,
  saveThreadModelName,
  THREAD_MODEL_KEY_PREFIX,
} from "@/core/settings/local";

class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length() {
    return this.map.size;
  }
  clear() {
    this.map.clear();
  }
  getItem(key: string) {
    return this.map.get(key) ?? null;
  }
  key(index: number) {
    return [...this.map.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.map.delete(key);
  }
  setItem(key: string, value: string) {
    this.map.set(key, String(value));
  }
}

describe("storage key migration (deerflow.* -> nova.*)", () => {
  let storage: MemoryStorage;

  beforeEach(() => {
    storage = new MemoryStorage();
  });

  test("maps nova.* keys to their legacy deerflow.* twin", () => {
    expect(legacyStorageKey("nova.local-settings")).toBe(
      "deerflow.local-settings",
    );
    expect(legacyStorageKey("unrelated")).toBeUndefined();
  });

  test("prefers the new key when present", () => {
    storage.setItem("nova.x", "new");
    storage.setItem("deerflow.x", "old");
    expect(getMigratedItem(storage, "nova.x")).toBe("new");
    expect(storage.getItem("deerflow.x")).toBe("old");
  });

  test("reads the legacy key and migrates it to the new key", () => {
    storage.setItem("deerflow.x", "old");
    expect(getMigratedItem(storage, "nova.x")).toBe("old");
    expect(storage.getItem("nova.x")).toBe("old");
    expect(storage.getItem("deerflow.x")).toBeNull();
  });

  test("returns null when neither key exists", () => {
    expect(getMigratedItem(storage, "nova.x")).toBeNull();
  });

  test("never throws when storage is unavailable", () => {
    const broken = {
      getItem: () => {
        throw new Error("SecurityError");
      },
    } as unknown as Storage;
    expect(getMigratedItem(broken, "nova.x")).toBeNull();
  });

  test("removeMigratedItem clears both keys", () => {
    storage.setItem("nova.x", "new");
    storage.setItem("deerflow.x", "old");
    removeMigratedItem(storage, "nova.x");
    expect(storage.length).toBe(0);
  });
});

describe("local settings keep the user's legacy values", () => {
  let storage: MemoryStorage;

  beforeEach(() => {
    storage = new MemoryStorage();
    vi.stubGlobal("window", {});
    vi.stubGlobal("localStorage", storage);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("uses the nova.* namespace", () => {
    expect(LOCAL_SETTINGS_KEY).toBe("nova.local-settings");
    expect(THREAD_MODEL_KEY_PREFIX).toBe("nova.thread-model.");
  });

  test("migrates deerflow.local-settings on first read", () => {
    storage.setItem(
      "deerflow.local-settings",
      JSON.stringify({ notification: { enabled: false } }),
    );
    expect(getLocalSettings().notification.enabled).toBe(false);
    expect(storage.getItem("deerflow.local-settings")).toBeNull();
    expect(storage.getItem(LOCAL_SETTINGS_KEY)).not.toBeNull();
  });

  test("migrates a legacy per-thread model choice", () => {
    storage.setItem("deerflow.thread-model.t1", "model-a");
    expect(getThreadModelName("t1")).toBe("model-a");
    expect(storage.getItem("nova.thread-model.t1")).toBe("model-a");
  });

  test("clearing a thread model removes the legacy key too", () => {
    storage.setItem("deerflow.thread-model.t1", "model-a");
    saveThreadModelName("t1", undefined);
    expect(getThreadModelName("t1")).toBeUndefined();
  });
});
