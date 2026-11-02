/**
 * Browser-storage key migration for the deerflow -> nova rename.
 *
 * Keys used to be namespaced `deerflow.*`. They are now `nova.*`. Users who
 * already have settings under the old key keep them: the first read copies
 * the legacy value to the new key and removes the legacy one. Remove this
 * fallback one release after the rename.
 */

export const LEGACY_STORAGE_PREFIX = "deerflow.";
export const STORAGE_PREFIX = "nova.";

/** Map a `nova.*` key to the `deerflow.*` key it replaced. */
export function legacyStorageKey(key: string): string | undefined {
  return key.startsWith(STORAGE_PREFIX)
    ? `${LEGACY_STORAGE_PREFIX}${key.slice(STORAGE_PREFIX.length)}`
    : undefined;
}

/**
 * Read `key` from `storage`, falling back to (and migrating) its legacy
 * `deerflow.*` twin. Storage errors (quota, privacy mode) never throw.
 */
export function getMigratedItem(storage: Storage, key: string): string | null {
  let value: string | null;
  try {
    value = storage.getItem(key);
  } catch {
    return null;
  }
  if (value !== null) {
    return value;
  }
  const legacyKey = legacyStorageKey(key);
  if (!legacyKey) {
    return null;
  }
  let legacy: string | null;
  try {
    legacy = storage.getItem(legacyKey);
  } catch {
    return null;
  }
  if (legacy === null) {
    return null;
  }
  try {
    storage.setItem(key, legacy);
    storage.removeItem(legacyKey);
  } catch {
    // Migration is best-effort; the legacy value is still returned.
  }
  return legacy;
}

/** Remove `key` and its legacy twin so a cleared value cannot resurface. */
export function removeMigratedItem(storage: Storage, key: string): void {
  storage.removeItem(key);
  const legacyKey = legacyStorageKey(key);
  if (legacyKey) {
    storage.removeItem(legacyKey);
  }
}
