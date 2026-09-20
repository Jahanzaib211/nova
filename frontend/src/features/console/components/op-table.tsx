"use client";

import { cn } from "@/lib/utils";

/** A dense, deterministic table for capability `Items` outputs. */
export function OpTable({
  columns,
  rows,
  empty,
  keyOf,
  className,
}: {
  columns: Array<{
    key: string;
    label: string;
    render?: (row: Record<string, unknown>) => React.ReactNode;
    className?: string;
  }>;
  rows: Array<Record<string, unknown>>;
  empty: string;
  keyOf: (row: Record<string, unknown>, index: number) => string;
  className?: string;
}) {
  if (rows.length === 0) {
    return <p className="text-muted-foreground text-sm">{empty}</p>;
  }
  return (
    <div className={cn("overflow-x-auto rounded-md border", className)}>
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-muted-foreground text-left text-xs uppercase">
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                className={cn("px-3 py-2 font-medium", c.className)}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={keyOf(row, i)} className="border-t">
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={cn("px-3 py-2 align-top", c.className)}
                >
                  {c.render ? c.render(row) : formatCell(row[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function formatCell(value: unknown): React.ReactNode {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function StatusDot({ tone }: { tone: "ok" | "warn" | "bad" | "off" }) {
  const cls = {
    ok: "bg-success",
    warn: "bg-warning",
    bad: "bg-destructive",
    off: "bg-muted-foreground/40",
  }[tone];
  return (
    <span
      aria-hidden="true"
      className={cn("inline-block size-2 rounded-full", cls)}
    />
  );
}

export function tsToLocal(value: unknown): string {
  if (typeof value === "number") return new Date(value * 1000).toLocaleString();
  if (typeof value === "string" && value)
    return new Date(value).toLocaleString();
  return "—";
}
