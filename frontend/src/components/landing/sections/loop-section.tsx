import { Section } from "../section";

const STEPS = [
  {
    number: "01",
    name: "Ask",
    body: "Describe the app, fix or audit in chat, over the API, in a messaging app, or from another agent over MCP.",
  },
  {
    number: "02",
    name: "Build",
    body: "A lead agent plans the work and hands parts to sub-agents, each inside an isolated sandbox.",
  },
  {
    number: "03",
    name: "Verify",
    body: "Nova starts the app, opens it in a headless browser, reads console and network errors, and reviews screenshots.",
  },
  {
    number: "04",
    name: "Secure",
    body: "Leaked secrets are caught when the agent writes a file, and look-alike packages are flagged before install. Deeper scans run on demand.",
  },
  {
    number: "05",
    name: "Ship",
    body: "You get the result with a full record of every command and change the agent made.",
  },
];

export function LoopSection({ className }: { className?: string }) {
  return (
    <Section
      id="loop"
      className={className}
      kicker="The production loop"
      title={<>One loop: ask, build, verify, secure, ship.</>}
      subtitle="Security is not a scan somebody remembers to run at the end. It sits inside the same loop that writes the code, on every task, before anything ships."
    >
      <ol className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        {STEPS.map((step) => (
          <li
            key={step.number}
            className="relative flex flex-col rounded-2xl border border-white/10 bg-white/[0.03] p-6"
          >
            <span
              className="absolute inset-x-6 top-0 h-px bg-gradient-to-r from-[#926dff]/70 to-transparent"
              aria-hidden="true"
            />
            <span className="font-mono text-xs font-semibold tracking-[0.16em] text-slate-400">
              {step.number}
            </span>
            <h3 className="mt-4 text-xl font-semibold text-white">
              {step.name}
            </h3>
            <p className="mt-3 text-sm leading-relaxed text-slate-400">
              {step.body}
            </p>
          </li>
        ))}
      </ol>
    </Section>
  );
}
