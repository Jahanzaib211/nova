import { Section } from "../section";

const LAYERS = [
  {
    name: "Model keys",
    body: "Customers bring their own keys. They are encrypted at rest and never shown again. Any model works, cloud or local.",
  },
  {
    name: "Tool access (MCP)",
    body: "Other agents reach Nova through scoped, revocable tokens. Admin and secret operations are never exposed.",
  },
  {
    name: "Outside agents (ACP)",
    body: "Run Claude Code and other agents under plan, standard or full permissions. Everything is denied by default.",
  },
  {
    name: "Sandbox",
    body: "Each task gets its own container with resource limits, command logging and a tamper-evident audit log.",
  },
  {
    name: "Platform",
    body: "Session protection, rate limits, role-based access, an append-only admin log and signed releases.",
  },
  {
    name: "Any model",
    body: "Claude, OpenAI-compatible APIs, DeepSeek, MiniMax, vLLM, or a local model on one GPU.",
  },
];

export function SecuritySection({ className }: { className?: string }) {
  return (
    <Section
      id="security"
      className={className}
      kicker={<span className="text-[#5eead4]">Security in the loop</span>}
      title={<>Security at every layer of the agent stack, from day one.</>}
      subtitle="Nova treats the agent stack as the attack surface. Every layer has a control that is on by default and fails closed."
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {LAYERS.map((layer, index) => (
          <div
            key={layer.name}
            className="rounded-2xl border border-white/10 bg-white/[0.03] p-6"
          >
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-xs text-slate-600">
                {String(index + 1).padStart(2, "0")}
              </span>
              <h3 className="text-base font-semibold text-white">
                {layer.name}
              </h3>
            </div>
            <p className="mt-3 text-sm leading-relaxed text-slate-400">
              {layer.body}
            </p>
          </div>
        ))}
      </div>
    </Section>
  );
}
