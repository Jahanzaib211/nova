"use client";

import { cn } from "@/lib/utils";

export function Empty({
  children,
  testId,
}: {
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className="border-panel-border text-muted-foreground rounded-lg border border-dashed px-4 py-8 text-center text-sm"
    >
      {children}
    </div>
  );
}

export function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("flex flex-col gap-1 text-xs", className)}>
      <span className="text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

export function Pill({
  tone,
  children,
  testId,
}: {
  tone: string;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <span
      data-testid={testId}
      className={cn(
        "inline-flex h-6 items-center rounded-md px-2 font-mono text-[11px] font-medium tabular-nums",
        tone,
      )}
    >
      {children}
    </span>
  );
}

export function Problems({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul
      className="text-warning-foreground dark:text-warning list-disc space-y-0.5 pl-5 text-xs"
      data-testid="problems"
    >
      {items.map((p) => (
        <li key={p}>{p}</li>
      ))}
    </ul>
  );
}
