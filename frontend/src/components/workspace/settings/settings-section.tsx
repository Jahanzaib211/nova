import { cn } from "@/lib/utils";

export function SettingsSection({
  className,
  title,
  description,
  action,
  children,
}: {
  className?: string;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Optional control rendered at the header's trailing edge (e.g. a refresh button). */
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className={cn(className)}>
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-2">
          <div className="text-lg font-semibold">{title}</div>
          {description && (
            <div className="text-muted-foreground text-sm">{description}</div>
          )}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </header>
      <main className="mt-4">{children}</main>
    </section>
  );
}
