import { Section } from "../section";

const STATS = [
  {
    stat: "84%",
    body: "of developers use or plan to use AI coding tools, and nearly half distrust the output.",
    source: "Stack Overflow Developer Survey 2025",
  },
  {
    stat: "45%",
    body: "of tasks in independent tests had AI-generated code introduce a security flaw.",
    source: "Veracode, 2025",
  },
  {
    stat: "1 in 10",
    body: "of 1,645 apps scanned on one popular vibe-coding platform exposed data through broken access rules.",
    source: "CVE-2025-48757",
  },
  {
    stat: "$4.44M",
    body: "average cost of a global breach, with heavy shadow-AI use adding about $670K.",
    source: "IBM / Ponemon, 2025",
  },
];

export function ProblemSection({ className }: { className?: string }) {
  return (
    <Section
      id="why-now"
      className={className}
      kicker="Why now"
      title={
        <>
          AI now writes most new code.{" "}
          <span className="text-shimmer" data-shimmer="text">
            Almost nobody checks it.
          </span>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {STATS.map((item) => (
          <div
            key={item.stat}
            className="flex flex-col rounded-2xl border border-white/10 bg-white/[0.03] p-6"
          >
            <span className="font-mono text-3xl font-semibold text-white">
              {item.stat}
            </span>
            <p className="mt-3 text-sm leading-relaxed text-slate-400">
              {item.body}
            </p>
            <p className="mt-5 text-xs text-slate-500">{item.source}</p>
          </div>
        ))}
      </div>

      <p className="mt-8 text-xs leading-relaxed text-slate-500">
        Sources: Veracode 2025; Stack Overflow Developer Survey 2025; IBM Cost
        of a Data Breach 2025; CVE-2025-48757.
      </p>
    </Section>
  );
}
