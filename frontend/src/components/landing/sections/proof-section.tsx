import { Section } from "../section";

const STATS = [
  {
    value: "Live",
    label:
      "Running in production today. Pre-revenue: this round pays for the first paid pilots.",
  },
  {
    value: "8,659",
    label: "automated tests across backend, frontend and end-to-end suites.",
  },
  {
    value: "285",
    label: "API operations, with 40 tools other AI agents can call over MCP.",
  },
  {
    value: "20",
    label:
      "CI pipelines including code scanning, container scanning and signed releases.",
  },
];

const BUYERS = [
  {
    name: "Software agencies",
    who: "10 to 200 people building client web apps with AI.",
    pain: "One leaked key or one broken access rule ends the contract.",
    trigger: "A client security questionnaire, or a lost deal over a finding.",
  },
  {
    name: "Regulated SMEs and mid-market",
    who: "Finance, healthcare, legal and public-sector suppliers.",
    pain: "Source code and data cannot leave the building.",
    trigger: "A policy that bans sending code to a cloud model.",
  },
  {
    name: "Security consultancies and MSSPs",
    who: "Consultancies delivering audits and managed security services.",
    pain: "Client work needs an isolated, logged agent, not a shared laptop.",
    trigger: "A contract that requires client work to stay in their tenancy.",
  },
  {
    name: "Technical founders",
    who: "Indie builders and small startups.",
    pain: "No budget for a hosted code assistant, and no security team.",
    trigger: "The first enterprise customer with a security review.",
  },
];

export function ProofSection({ className }: { className?: string }) {
  return (
    <Section
      id="proof"
      className={className}
      kicker="Where we are"
      title="Built, live and ready for pilots"
      subtitle="The platform is running in production. These are measured numbers, not projections."
    >
      <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {STATS.map((stat) => (
          <div
            key={stat.label}
            className="rounded-2xl border border-white/10 bg-white/[0.03] p-6"
          >
            <dt className="font-mono text-3xl font-semibold text-white">
              {stat.value}
            </dt>
            <dd className="mt-3 text-sm leading-relaxed text-slate-400">
              {stat.label}
            </dd>
          </div>
        ))}
      </dl>

      <div className="mt-16">
        <h3 className="text-xl font-semibold text-white">Who buys first</h3>
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {BUYERS.map((buyer) => (
            <div
              key={buyer.name}
              className="flex flex-col rounded-xl border border-white/10 bg-white/[0.02] p-5"
            >
              <h4 className="text-sm font-semibold text-white">{buyer.name}</h4>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">
                {buyer.who}
              </p>
              <dl className="mt-4 space-y-2 border-t border-white/10 pt-4 text-sm">
                <div>
                  <dt className="font-mono text-[11px] tracking-wide text-slate-400 uppercase">
                    Pain
                  </dt>
                  <dd className="mt-1 text-slate-400">{buyer.pain}</dd>
                </div>
                <div>
                  <dt className="font-mono text-[11px] tracking-wide text-slate-400 uppercase">
                    Trigger
                  </dt>
                  <dd className="mt-1 text-slate-400">{buyer.trigger}</dd>
                </div>
              </dl>
            </div>
          ))}
        </div>
      </div>
    </Section>
  );
}
