/**
 * Per-browser model visibility.
 *
 * `Model.hidden` comes from the server: entries in `config.yaml` are
 * hand-maintained (env placeholders and comments must survive) and the models
 * API only writes runtime models, so the UI cannot flip that flag for a
 * configured model. Overrides therefore live here, in localStorage, and let a
 * user offer any configured model in the chat picker without touching
 * `config.yaml`:
 *
 * - no override → follow the server's `hidden` flag
 * - `true`      → offer it in the chat picker
 * - `false`     → keep it out of the chat picker
 *
 * `showHidden` is a separate, transient preference: it reveals every model in
 * the picker (badged as hidden) without changing any per-model override, so
 * "let me pick one of these for a moment" never silently rewrites the curated
 * list.
 */

import type { Model } from "./types";

export const MODEL_VISIBILITY_KEY = "nova.model-visibility";

export interface ModelVisibilityState {
  /** Model name → true (offer in chat) / false (keep out of chat). */
  overrides: Record<string, boolean>;
  /** Reveal every model in the picker, including hidden ones. */
  showHidden: boolean;
}

export type ModelVisibilitySnapshot = ModelVisibilityState;

const EMPTY_STATE: ModelVisibilityState = { overrides: {}, showHidden: false };

let snapshot: ModelVisibilityState | null = null;
let storageListenerRegistered = false;
const listeners = new Set<() => void>();

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

function sanitize(value: unknown): ModelVisibilityState {
  if (!value || typeof value !== "object") {
    return EMPTY_STATE;
  }
  const raw = value as Partial<ModelVisibilityState>;
  const overrides: Record<string, boolean> = {};
  if (raw.overrides && typeof raw.overrides === "object") {
    for (const [name, visible] of Object.entries(raw.overrides)) {
      if (typeof visible === "boolean") {
        overrides[name] = visible;
      }
    }
  }
  return { overrides, showHidden: raw.showHidden === true };
}

function readState(): ModelVisibilityState {
  if (!isBrowser()) {
    return EMPTY_STATE;
  }
  try {
    const stored = window.localStorage.getItem(MODEL_VISIBILITY_KEY);
    return stored ? sanitize(JSON.parse(stored)) : EMPTY_STATE;
  } catch {
    return EMPTY_STATE;
  }
}

function writeState(state: ModelVisibilityState): void {
  if (!isBrowser()) {
    return;
  }
  try {
    window.localStorage.setItem(MODEL_VISIBILITY_KEY, JSON.stringify(state));
  } catch {
    // Private mode / quota: keep the in-memory value for this session.
  }
}

function emitChange(): void {
  for (const listener of listeners) {
    listener();
  }
}

function commit(next: ModelVisibilityState): void {
  snapshot = next;
  writeState(next);
  emitChange();
}

function handleStorage(event: StorageEvent): void {
  if (event.storageArea && event.storageArea !== window.localStorage) {
    return;
  }
  if (event.key !== null && event.key !== MODEL_VISIBILITY_KEY) {
    return;
  }
  snapshot = readState();
  emitChange();
}

export function subscribeModelVisibility(listener: () => void): () => void {
  listeners.add(listener);
  if (!storageListenerRegistered && isBrowser()) {
    window.addEventListener("storage", handleStorage);
    storageListenerRegistered = true;
  }
  return () => {
    listeners.delete(listener);
  };
}

/** Stable snapshot for `useSyncExternalStore` (read once, cached). */
export function getModelVisibilitySnapshot(): ModelVisibilityState {
  snapshot ??= readState();
  return snapshot;
}

/** Server render has no storage: nothing hidden beyond the server flags. */
export function getModelVisibilityServerSnapshot(): ModelVisibilityState {
  return EMPTY_STATE;
}

export function setModelVisible(name: string, visible: boolean): void {
  const current = getModelVisibilitySnapshot();
  commit({
    ...current,
    overrides: { ...current.overrides, [name]: visible },
  });
}

/** Drop the override so the model follows the server's `hidden` flag again. */
export function clearModelVisibility(name: string): void {
  const current = getModelVisibilitySnapshot();
  if (!(name in current.overrides)) {
    return;
  }
  const overrides = { ...current.overrides };
  delete overrides[name];
  commit({ ...current, overrides });
}

export function setShowHiddenModels(showHidden: boolean): void {
  const current = getModelVisibilitySnapshot();
  if (current.showHidden === showHidden) {
    return;
  }
  commit({ ...current, showHidden });
}

export function resetModelVisibility(): void {
  commit(EMPTY_STATE);
}

/** Is this model offered in the chat picker? Override first, server flag next. */
export function isModelVisible(
  model: Pick<Model, "name" | "hidden">,
  state: ModelVisibilityState = getModelVisibilitySnapshot(),
): boolean {
  const override = state.overrides[model.name];
  return typeof override === "boolean" ? override : !(model.hidden ?? false);
}

/**
 * What the picker renders: everything offered, plus everything else while
 * "show hidden" is on, plus the model the conversation is already using (a
 * saved choice must stay visible even if it was hidden since).
 */
export function offeredModels<M extends Pick<Model, "name" | "hidden">>(
  models: readonly M[],
  state: ModelVisibilityState,
  selectedName?: unknown,
): M[] {
  return models.filter(
    (model) =>
      state.showHidden ||
      isModelVisible(model, state) ||
      model.name === selectedName,
  );
}
