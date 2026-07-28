import { cn } from "@/lib/utils";

export function Section({
  className,
  title,
  subtitle,
  children,
}: {
  className?: string;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        "mx-auto flex flex-col px-4 py-12 md:px-0 md:py-16",
        className,
      )}
    >
      <header className="flex flex-col items-center justify-between">
        <div className="mb-4 bg-linear-to-r from-white via-gray-200 to-gray-400 bg-clip-text text-center text-3xl font-bold text-transparent sm:text-4xl md:text-5xl">
          {title}
        </div>
        {subtitle && (
          <div className="text-muted-foreground text-center text-base sm:text-lg md:text-xl">
            {subtitle}
          </div>
        )}
      </header>
      <main className="mt-4">{children}</main>
    </section>
  );
}
