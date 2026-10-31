import { cn } from "@/lib/utils";

export type SectionProps = {
  id?: string;
  className?: string;
  kicker?: React.ReactNode;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
  /** A hairline above the section. Off for the first section under the hero. */
  divider?: boolean;
};

/**
 * The shared shell for every landing section: a kicker, one heading, one
 * line of context and then the content. The page is dark by design, so the
 * kicker stays neutral and the section leaves the accent colours to the
 * content it holds.
 */
export function Section({
  id,
  className,
  kicker,
  title,
  subtitle,
  children,
  divider = true,
}: SectionProps) {
  return (
    <section
      id={id}
      className={cn(
        "relative mx-auto w-full max-w-6xl scroll-mt-24 px-4 py-16 md:px-6 md:py-20",
        className,
      )}
    >
      {divider && (
        <hr className="mx-auto mb-12 h-px w-full border-none bg-gradient-to-r from-transparent via-white/10 to-transparent md:mb-16" />
      )}
      <header className="max-w-3xl">
        {kicker && (
          <p className="mb-4 flex items-center gap-3 text-xs font-semibold tracking-[0.2em] text-slate-500 uppercase">
            <span
              className="h-px w-8 bg-gradient-to-r from-[#926dff] to-transparent"
              aria-hidden="true"
            />
            {kicker}
          </p>
        )}
        <h2 className="text-3xl font-semibold tracking-tight text-balance text-white sm:text-4xl md:text-5xl">
          {title}
        </h2>
        {subtitle && (
          <p className="mt-5 max-w-2xl text-base leading-relaxed text-slate-400 sm:text-lg">
            {subtitle}
          </p>
        )}
      </header>
      <div className="mt-10 md:mt-14">{children}</div>
    </section>
  );
}
