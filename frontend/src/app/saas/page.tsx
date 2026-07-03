import { type Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Nova Cloud — Plans & Pricing",
  description:
    "Run Nova, the computer agent, your way: self-host for free or let Ali Technologies run it for you. A self-bootstrapped business built on the open Nova stack.",
};

const plans = [
  {
    name: "Self-Host",
    price: "Free",
    period: "forever",
    description:
      "Run the full Nova stack on your own machine or server. MIT-licensed foundation, your hardware, your keys.",
    features: [
      "Full agent harness + Agent's Computer UI",
      "Per-thread Docker sandbox isolation",
      "Bring your own LLM provider keys",
      "Community support on GitHub",
    ],
    cta: { label: "Get the code", href: "https://github.com/Jahanzaib211/nova" },
    highlight: false,
  },
  {
    name: "Nova Cloud",
    price: "$29",
    period: "per seat / month",
    description:
      "A managed Nova instance run by Ali Technologies: hosted sandboxes, updates, and monitoring — you just chat.",
    features: [
      "Managed hosting, updates & backups",
      "Isolated cloud sandboxes per conversation",
      "Watchdogs, receipts & reliability tooling",
      "Priority email support",
    ],
    cta: { label: "Request access", href: "mailto:alilabsx@gmail.com?subject=Nova%20Cloud%20access" },
    highlight: true,
  },
  {
    name: "Enterprise",
    price: "Custom",
    period: "annual",
    description:
      "Private deployment on your infrastructure with custom skills, integrations, and an SLA.",
    features: [
      "On-prem or private-cloud deployment",
      "Custom skills & IM channel integrations",
      "Security review & isolation hardening",
      "Dedicated support with SLA",
    ],
    cta: { label: "Talk to us", href: "mailto:alilabsx@gmail.com?subject=Nova%20Enterprise" },
    highlight: false,
  },
];

export default function SaasPage() {
  return (
    <main className="bg-background min-h-screen">
      <div className="mx-auto max-w-6xl px-6 py-16">
        <header className="text-center">
          <Link
            href="/"
            className="bg-gradient-to-r from-violet-600 to-cyan-600 bg-clip-text font-serif text-2xl font-semibold tracking-wide text-transparent dark:from-violet-400 dark:to-cyan-300"
          >
            Nova
          </Link>
          <h1 className="text-foreground mt-6 text-4xl font-semibold tracking-tight sm:text-5xl">
            The Agent&apos;s Computer, as a service
          </h1>
          <p className="text-muted-foreground mx-auto mt-4 max-w-2xl text-lg leading-relaxed">
            Nova is self-bootstrapped: the same instance that builds the product
            also serves the customers. Start free on your own hardware, or let
            Ali Technologies run it for you.
          </p>
        </header>

        <section className="mt-14 grid gap-6 md:grid-cols-3">
          {plans.map((plan) => (
            <div
              key={plan.name}
              className={
                plan.highlight
                  ? "border-primary/40 bg-card relative flex flex-col rounded-2xl border-2 p-7 shadow-lg"
                  : "border-border bg-card flex flex-col rounded-2xl border p-7"
              }
            >
              {plan.highlight && (
                <span className="bg-gradient-to-r from-violet-600 to-cyan-600 absolute -top-3 left-1/2 -translate-x-1/2 rounded-full px-3 py-0.5 text-xs font-medium text-white">
                  Most popular
                </span>
              )}
              <h2 className="text-foreground text-lg font-semibold">
                {plan.name}
              </h2>
              <div className="mt-3 flex items-baseline gap-2">
                <span className="text-foreground text-3xl font-semibold">
                  {plan.price}
                </span>
                <span className="text-muted-foreground text-sm">
                  {plan.period}
                </span>
              </div>
              <p className="text-muted-foreground mt-3 text-sm leading-relaxed">
                {plan.description}
              </p>
              <ul className="mt-5 flex-1 space-y-2">
                {plan.features.map((f) => (
                  <li
                    key={f}
                    className="text-foreground/90 flex items-start gap-2 text-sm"
                  >
                    <svg
                      className="text-primary mt-0.5 size-4 shrink-0"
                      viewBox="0 0 20 20"
                      fill="none"
                      aria-hidden="true"
                    >
                      <path
                        d="M4 10.5l4 4 8-9"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    {f}
                  </li>
                ))}
              </ul>
              <a
                href={plan.cta.href}
                className={
                  plan.highlight
                    ? "mt-7 inline-flex cursor-pointer items-center justify-center rounded-lg bg-gradient-to-r from-violet-600 to-cyan-600 px-4 py-2.5 text-sm font-medium text-white transition hover:opacity-90"
                    : "border-border text-foreground hover:bg-accent mt-7 inline-flex cursor-pointer items-center justify-center rounded-lg border px-4 py-2.5 text-sm font-medium transition"
                }
              >
                {plan.cta.label}
              </a>
            </div>
          ))}
        </section>

        <section className="mx-auto mt-16 max-w-3xl text-center">
          <h2 className="text-foreground text-2xl font-semibold">
            Self-bootstrapped, on purpose
          </h2>
          <p className="text-muted-foreground mt-3 leading-relaxed">
            Nova builds Nova. The agent maintains its own codebase, tests its
            own releases, and serves its own customers — which keeps costs near
            zero and lets revenue from Nova Cloud fund the open self-host
            edition instead of outside capital.
          </p>
          <p className="text-muted-foreground mt-6 text-sm">
            Made by{" "}
            <a
              href="https://www.alilabsx.com"
              target="_blank"
              rel="noopener noreferrer"
              className="text-foreground cursor-pointer underline underline-offset-4 hover:opacity-80"
            >
              Ali Technologies
            </a>{" "}
            · <a className="cursor-pointer underline underline-offset-4 hover:opacity-80" href="mailto:alilabsx@gmail.com">alilabsx@gmail.com</a>
          </p>
        </section>
      </div>
    </main>
  );
}
