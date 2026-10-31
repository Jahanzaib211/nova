import { ArrowRightIcon } from "lucide-react";
import Link from "next/link";

import { Section } from "../section";

const TIERS = [
  {
    name: "Community",
    price: "Free",
    note: "forever",
    features: ["Open-source core (MIT)", "Self-hosted", "Community support"],
    highlight: false,
  },
  {
    name: "Plus",
    price: "$49",
    note: "per seat / month",
    features: [
      "Agent's Computer UI",
      "Verify loop",
      "Secure-Ship (beta)",
      "Email support",
    ],
    highlight: true,
  },
  {
    name: "Enterprise",
    price: "from $12,000",
    note: "per year",
    features: [
      "On-prem licence",
      "Isolation",
      "Watchdog",
      "SSO roadmap",
      "SLA",
      "Priority support",
    ],
    highlight: false,
  },
  {
    name: "Services",
    price: "from $5,000",
    note: "one-off",
    features: [
      "Deployment",
      "Local-model setup",
      "Hardening",
      "Delivered with our partner",
    ],
    highlight: false,
  },
];

export function PricingTeaserSection({ className }: { className?: string }) {
  return (
    <Section
      id="pricing"
      className={className}
      kicker="Open core"
      title={
        <>
          Open core:{" "}
          <span className="text-shimmer" data-shimmer="text">
            free to start, paid to scale
          </span>
        </>
      }
      subtitle="Run the full stack yourself at no cost. Pay when a team needs the Agent's Computer, an on-premise licence or a supported deployment."
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {TIERS.map((tier) => (
          <div
            key={tier.name}
            className={
              tier.highlight
                ? "rounded-2xl border border-[#926dff]/40 bg-white/[0.05] p-6"
                : "rounded-2xl border border-white/10 bg-white/[0.03] p-6"
            }
          >
            <h3 className="text-sm font-semibold text-white">{tier.name}</h3>
            <p className="mt-3 font-mono text-2xl font-semibold text-white">
              {tier.price}
            </p>
            <p className="mt-1 text-xs text-slate-500">{tier.note}</p>
            <ul className="mt-5 space-y-2 border-t border-white/10 pt-4">
              {tier.features.map((feature) => (
                <li
                  key={feature}
                  className="text-sm leading-relaxed text-slate-400"
                >
                  {feature}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="mt-8 flex flex-wrap items-center gap-x-6 gap-y-3">
        <Link
          href="/saas"
          className="inline-flex items-center gap-2 text-sm font-semibold text-white transition hover:text-[#c4b5fd]"
        >
          See full pricing and features
          <ArrowRightIcon className="size-4" aria-hidden="true" />
        </Link>
        <span className="text-xs text-slate-500">
          Prices are proposals we test in pilots.
        </span>
      </div>
    </Section>
  );
}
