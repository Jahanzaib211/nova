import {
  ArrowRightIcon,
  CheckIcon,
  GithubIcon,
  MailIcon,
  ShieldCheckIcon,
  SparklesIcon,
} from "lucide-react";
import { type Metadata } from "next";
import Link from "next/link";

import { Footer } from "@/components/landing/footer";
import { Header } from "@/components/landing/header";
import { Button } from "@/components/ui/button";
import { ThreeBackground } from "@/components/ui/three-background";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "Nova — Plans & Pricing",
  description:
    "Nova is the AI developer for companies that can't send their code to the cloud. Self-host for free, or start a paid pilot from $49 per seat per month.",
};

const plans = [
  {
    name: "Community",
    price: "Free",
    period: "forever",
    description:
      "The open-source core (MIT) on your own machine or server. Your hardware, your keys.",
    features: [
      "Full agent harness + Agent's Computer UI",
      "40 verified security tools in every sandbox",
      "Bring your own LLM keys, cloud or local",
      "Community support on GitHub",
    ],
    cta: {
      label: "Get the code",
      href: "https://github.com/Jahanzaib211/nova",
      external: true,
    },
    highlight: false,
  },
  {
    name: "Plus",
    price: "$49",
    period: "per seat / month",
    description:
      "For teams that want the Agent's Computer, the verify loop and Secure-Ship on their own hardware.",
    features: [
      "Everything in Community",
      "Agent's Computer UI + verify loop",
      "Secure-Ship (beta): pre-deploy security gate",
      "Email support",
    ],
    cta: {
      label: "Start a pilot",
      href: "mailto:alilabsx@gmail.com?subject=Nova%20Plus%20pilot",
      external: false,
    },
    highlight: true,
  },
  {
    name: "Enterprise",
    price: "from $12,000",
    period: "per year",
    description:
      "An on-premise licence for regulated teams: isolation, watchdog and an SLA, deployed with our partner.",
    features: [
      "On-prem licence and isolation",
      "Watchdog + hash-chained audit trail",
      "SSO on the roadmap",
      "SLA and priority support",
    ],
    cta: {
      label: "Talk to us",
      href: "mailto:alilabsx@gmail.com?subject=Nova%20Enterprise",
      external: false,
    },
    highlight: false,
  },
  {
    name: "Services",
    price: "from $5,000",
    period: "one-off",
    description:
      "Deployment, local-model setup and hardening, delivered with Apex Global Tech Solutions.",
    features: [
      "Install, configure and harden",
      "Local-model setup on your own GPU",
      "30-day on-prem pilot, fee credited on purchase",
      "Delivered by our partner",
    ],
    cta: {
      label: "Book deployment",
      href: "mailto:alilabsx@gmail.com?subject=Nova%20deployment",
      external: false,
    },
    highlight: false,
  },
];

const proof = [
  ["40", "verified security tools in every sandbox"],
  ["8,659", "automated tests across backend, frontend and E2E"],
  ["285", "API operations, with 40 tools over MCP"],
  ["Any model", "cloud APIs or a local model on one GPU"],
];

const buyers = [
  {
    name: "Software agencies",
    body: "10 to 200 people building client web apps with AI. Ship without leaked keys or broken access rules.",
  },
  {
    name: "Regulated SMEs",
    body: "Finance, healthcare, legal and public-sector suppliers that can't send code or data to cloud AI.",
  },
  {
    name: "Security teams",
    body: "Consultancies and MSSPs that need an isolated, logged toolkit for building and auditing client work.",
  },
];

function Check() {
  return (
    <span
      className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-white/5"
      aria-hidden="true"
    >
      <CheckIcon className="size-3 text-[#c4b5fd]" />
    </span>
  );
}

export default function SaasPage() {
  return (
    <div className="relative min-h-screen w-full overflow-x-clip bg-[#0a0a0a] text-white">
      <div
        className="pointer-events-none fixed inset-0 -z-10"
        aria-hidden="true"
      >
        <ThreeBackground intensity={0.38} speed={0.28} />
        <div className="absolute inset-0 bg-[radial-gradient(90%_60%_at_50%_0%,rgba(124,58,237,0.16),transparent_65%)]" />
        <div className="absolute inset-0 bg-[linear-gradient(to_bottom,rgba(10,10,10,0.25),#0a0a0a_78%)]" />
      </div>

      <Header homeURL="/" className="text-white" tone="dark" nav="landing" />

      <main className="relative">
        {/* Hero */}
        <section className="mx-auto max-w-6xl px-6 pt-36 pb-16 text-center sm:pt-40">
          <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3.5 py-1 text-[11px] font-medium tracking-[0.18em] text-slate-300 uppercase backdrop-blur-sm">
            <SparklesIcon
              className="size-3.5 text-[#c4b5fd]"
              aria-hidden="true"
            />
            Plans &amp; pricing
          </span>
          <h1 className="mx-auto mt-6 max-w-4xl text-4xl font-semibold tracking-tight text-balance sm:text-5xl lg:text-6xl">
            The AI developer for companies that{" "}
            <span className="text-shimmer" data-shimmer="text">
              can&apos;t send their code to the cloud
            </span>
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-slate-400 sm:text-lg">
            Nova runs on your own servers. It builds web apps, tests them in a
            real browser, and checks them for security holes before they go
            live.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Button
              asChild
              size="lg"
              className="shimmer-sweep bg-brand-gradient h-11 rounded-lg px-6 text-sm font-semibold text-[#0a0a0a] hover:opacity-90"
              data-shimmer="sweep"
            >
              <Link href="/workspace">
                Start building
                <ArrowRightIcon className="size-4" aria-hidden="true" />
              </Link>
            </Button>
            <Button
              asChild
              size="lg"
              variant="outline"
              className="h-11 rounded-lg border-white/15 bg-transparent px-6 text-sm font-semibold text-white hover:border-white/30 hover:bg-white/5 hover:text-white"
            >
              <a href="mailto:alilabsx@gmail.com?subject=Nova%20pilot">
                <MailIcon className="size-4" aria-hidden="true" />
                Talk to us
              </a>
            </Button>
          </div>
          <p className="mt-5 text-xs text-slate-500">
            Open core · Self-hosted · MIT licensed
          </p>
        </section>

        {/* Plans */}
        <section
          aria-label="Nova plans"
          className="mx-auto max-w-7xl px-4 pb-8 sm:px-6"
        >
          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
            {plans.map((plan) => (
              <div
                key={plan.name}
                className={cn(
                  "group relative flex rounded-3xl transition-transform duration-300 motion-safe:hover:-translate-y-1",
                  plan.highlight
                    ? "bg-brand-gradient p-px shadow-[0_0_50px_-12px_rgba(124,58,237,0.55)]"
                    : "border border-white/10 bg-white/[0.03]",
                )}
              >
                {plan.highlight && (
                  <span className="bg-brand-gradient absolute -top-3 left-1/2 z-10 -translate-x-1/2 rounded-full px-3 py-1 text-[10px] font-semibold tracking-[0.14em] text-[#0a0a0a] uppercase">
                    Most popular
                  </span>
                )}
                <article
                  aria-label={`${plan.name} plan`}
                  className={cn(
                    "flex w-full flex-col rounded-[calc(1.5rem-1px)] p-6",
                    plan.highlight
                      ? "bg-[#0d0b14]"
                      : "bg-transparent group-hover:border-white/20",
                  )}
                >
                  <div className="flex items-center justify-between">
                    <h2 className="text-base font-semibold text-white">
                      {plan.name}
                    </h2>
                    {plan.name === "Plus" && (
                      <ShieldCheckIcon
                        className="size-4 text-[#5eead4]"
                        aria-hidden="true"
                      />
                    )}
                  </div>
                  <div className="mt-4 flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <span
                      className={cn(
                        "text-3xl font-semibold tracking-tight",
                        plan.highlight ? "text-brand-gradient" : "text-white",
                      )}
                    >
                      {plan.price}
                    </span>
                    <span className="text-xs text-slate-500">
                      {plan.period}
                    </span>
                  </div>
                  <p className="mt-4 min-h-20 text-sm leading-relaxed text-slate-400">
                    {plan.description}
                  </p>
                  <ul className="mt-5 flex-1 space-y-2.5 border-t border-white/10 pt-5">
                    {plan.features.map((feature) => (
                      <li
                        key={feature}
                        className="flex items-start gap-2.5 text-sm leading-snug text-slate-300"
                      >
                        <Check />
                        {feature}
                      </li>
                    ))}
                  </ul>
                  <a
                    href={plan.cta.href}
                    target={plan.cta.external ? "_blank" : undefined}
                    rel={plan.cta.external ? "noopener noreferrer" : undefined}
                    className={cn(
                      "mt-7 inline-flex h-10 cursor-pointer items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold transition focus-visible:ring-2 focus-visible:ring-[#c4b5fd] focus-visible:outline-none",
                      plan.highlight
                        ? "bg-brand-gradient text-[#0a0a0a] hover:opacity-90"
                        : "border border-white/15 text-white hover:border-white/30 hover:bg-white/5",
                    )}
                  >
                    {plan.cta.label}
                    {plan.cta.external ? (
                      <GithubIcon className="size-4" aria-hidden="true" />
                    ) : (
                      <ArrowRightIcon className="size-4" aria-hidden="true" />
                    )}
                  </a>
                </article>
              </div>
            ))}
          </div>
          <p className="mx-auto mt-8 max-w-2xl text-center text-sm leading-relaxed text-slate-500">
            Prices are proposals we test in pilots. Stripe billing, referrals
            and bring-your-own-key are already built.
          </p>
        </section>

        {/* Proof */}
        <section
          aria-label="Nova in numbers"
          className="mx-auto mt-16 max-w-6xl px-6"
        >
          <div className="grid gap-px overflow-hidden rounded-2xl border border-white/10 bg-white/10 sm:grid-cols-2 lg:grid-cols-4">
            {proof.map(([value, caption]) => (
              <div key={caption} className="bg-[#0a0a0a] px-6 py-7">
                <div className="text-brand-gradient text-2xl font-semibold tracking-tight">
                  {value}
                </div>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">
                  {caption}
                </p>
              </div>
            ))}
          </div>
        </section>

        {/* Who buys first */}
        <section className="mx-auto mt-24 max-w-6xl px-6">
          <div className="text-center">
            <p className="text-[11px] font-medium tracking-[0.2em] text-[#c4b5fd] uppercase">
              Early customers
            </p>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight sm:text-3xl">
              Who buys first
            </h2>
          </div>
          <div className="mt-9 grid gap-4 md:grid-cols-3">
            {buyers.map((buyer) => (
              <article
                key={buyer.name}
                className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 transition-colors hover:border-white/20"
              >
                <h3 className="text-base font-semibold text-white">
                  {buyer.name}
                </h3>
                <p className="mt-2.5 text-sm leading-relaxed text-slate-400">
                  {buyer.body}
                </p>
              </article>
            ))}
          </div>
        </section>

        {/* Open core */}
        <section className="mx-auto mt-24 max-w-5xl px-6">
          <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-white/[0.03] px-6 py-10 text-center sm:px-12">
            <div
              className="bg-brand-gradient pointer-events-none absolute -top-24 left-1/2 h-48 w-[36rem] -translate-x-1/2 rounded-full opacity-10 blur-3xl"
              aria-hidden="true"
            />
            <h2 className="relative text-2xl font-semibold tracking-tight sm:text-3xl">
              Open core, partner-led
            </h2>
            <p className="relative mx-auto mt-4 max-w-3xl leading-relaxed text-slate-400">
              The MIT-licensed foundation and Nova&apos;s open-source additions
              drive adoption. The Agent&apos;s Computer, verify loop, security
              layer and operations are proprietary. Enterprise installs are
              sold, deployed and supported with Apex Global Tech Solutions.
            </p>
            <p className="relative mt-4 text-sm text-slate-500">
              30-day on-premise pilots convert to annual licences, and the pilot
              fee is credited on purchase.
            </p>
            <div className="relative mt-8 flex flex-wrap items-center justify-center gap-3">
              <Button
                asChild
                className="bg-brand-gradient h-10 rounded-lg px-5 text-sm font-semibold text-[#0a0a0a] hover:opacity-90"
              >
                <Link href="/workspace">Start building</Link>
              </Button>
              <a
                href="https://github.com/Jahanzaib211/nova"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-white/15 px-5 text-sm font-semibold text-white transition hover:border-white/30 hover:bg-white/5"
              >
                <GithubIcon className="size-4" aria-hidden="true" />
                Get the code
              </a>
            </div>
            <p className="relative mt-8 text-xs text-slate-500">
              Made by{" "}
              <a
                href="https://www.alilabsx.com"
                target="_blank"
                rel="noopener noreferrer"
                className="text-slate-300 underline underline-offset-4 transition hover:text-white"
              >
                Ali Technologies
              </a>{" "}
              ·{" "}
              <a
                href="mailto:alilabsx@gmail.com"
                className="text-slate-300 underline underline-offset-4 transition hover:text-white"
              >
                alilabsx@gmail.com
              </a>{" "}
              ·{" "}
              <a
                href="https://nova.alilabsx.com"
                target="_blank"
                rel="noopener noreferrer"
                className="text-slate-300 underline underline-offset-4 transition hover:text-white"
              >
                nova.alilabsx.com
              </a>
            </p>
          </div>
        </section>
      </main>

      <div className="mt-20">
        <Footer />
      </div>
    </div>
  );
}
