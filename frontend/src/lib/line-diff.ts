// Minimal line-level diff (LCS) for the Editor's live-diff view.
// Returns an interleaved sequence of context / added / removed lines so the
// Agent's Computer can render a zai/cursor-style red/green diff as the agent edits.

export type DiffLine = {
  type: "ctx" | "add" | "del";
  text: string;
  /** 1-based line number in the old file (null for added lines). */
  oldNo: number | null;
  /** 1-based line number in the new file (null for removed lines). */
  newNo: number | null;
};

/**
 * Compute a line diff between `oldText` and `newText` using an LCS table.
 * Suitable for the typical edit sizes the agent produces (a few hundred lines);
 * it is O(n*m) in lines, which is fine here and keeps the implementation small.
 */
export function lineDiff(oldText: string, newText: string): DiffLine[] {
  const a = oldText.length ? oldText.split("\n") : [];
  const b = newText.length ? newText.split("\n") : [];
  const n = a.length;
  const m = b.length;

  // LCS length table.
  const lcs: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i]![j] =
        a[i] === b[j]
          ? lcs[i + 1]![j + 1]! + 1
          : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  let oldNo = 1;
  let newNo = 1;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ type: "ctx", text: a[i]!, oldNo: oldNo++, newNo: newNo++ });
      i++;
      j++;
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) {
      out.push({ type: "del", text: a[i]!, oldNo: oldNo++, newNo: null });
      i++;
    } else {
      out.push({ type: "add", text: b[j]!, oldNo: null, newNo: newNo++ });
      j++;
    }
  }
  while (i < n)
    out.push({ type: "del", text: a[i++]!, oldNo: oldNo++, newNo: null });
  while (j < m)
    out.push({ type: "add", text: b[j++]!, oldNo: null, newNo: newNo++ });
  return out;
}

export type DiffStats = { added: number; removed: number };

export function diffStats(lines: DiffLine[]): DiffStats {
  let added = 0;
  let removed = 0;
  for (const l of lines) {
    if (l.type === "add") added++;
    else if (l.type === "del") removed++;
  }
  return { added, removed };
}
