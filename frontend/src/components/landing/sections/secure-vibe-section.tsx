import { CheckIcon, FlaskConicalIcon } from "lucide-react";

import { Section } from "../section";

const LIVE = [
  "40 security tools in every sandbox covering recon, vulnerabilities, web apps, secrets and defence.",
  "Leaked secrets are flagged when the agent writes a file, and look-alike npm packages are flagged before install.",
  "High-risk shell commands are blocked, and every command is written to the audit log.",
  "A security skill ships with recon, web-app, secrets and hardening playbooks.",
];

const BETA = [
  "Secure-Ship: an automatic pre-deploy gate for secrets, vulnerable dependencies, container findings and unsafe configuration. Critical or high findings block the handover.",
  "The gate writes a JSON and Markdown report next to the build, so every finding can be reviewed.",
  "Deep scans through Trivy cover vulnerabilities, secrets and misconfiguration on demand.",
  "New checks and policy defaults ship to pilot customers first.",
];

export function SecureVibeSection({ className }: { className?: string }) {
  return (
    <Section
      id="secure-vibe-coding"
      className={className}
      kicker="Secure vibe coding (beta)"
      title={<>Code fast. Ship safe.</>}
      subtitle="Most of the security tooling is already in the product. The rest is on the way, and it ships to pilot customers first."
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 md:p-8">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
            <CheckIcon className="size-4 text-[#5eead4]" aria-hidden="true" />
            Live today
          </h3>
          <ul className="mt-6 space-y-4">
            {LIVE.map((item) => (
              <li key={item} className="flex items-start gap-3">
                <span
                  className="mt-2 size-1.5 shrink-0 rounded-full bg-[#5eead4]"
                  aria-hidden="true"
                />
                <p className="text-sm leading-relaxed text-slate-300">{item}</p>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 md:p-8">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
            <FlaskConicalIcon
              className="size-4 text-[#fbbf24]"
              aria-hidden="true"
            />
            In beta, shipping next
          </h3>
          <ul className="mt-6 space-y-4">
            {BETA.map((item) => (
              <li key={item} className="flex items-start gap-3">
                <span
                  className="mt-2 size-1.5 shrink-0 rounded-full bg-slate-600"
                  aria-hidden="true"
                />
                <p className="text-sm leading-relaxed text-slate-400">{item}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <p className="mt-8 text-xs text-slate-500">
        Beta features are available to pilot customers. Scope may change.
      </p>
    </Section>
  );
}
