import { CheckIcon } from "lucide-react";
import Image from "next/image";

import { Section } from "../section";

const POINTS = [
  "Runs on your servers or a single GPU workstation, with cloud or fully local models.",
  "Writes the app, starts it, opens it in a real browser and fixes what breaks.",
  "Every task runs in its own sandbox with 40 security tools built in.",
  "You watch every file, command and change live, with a full audit trail.",
];

export function ProductSection({ className }: { className?: string }) {
  return (
    <Section
      id="product"
      className={className}
      kicker="The product"
      title={
        <>
          Describe the app. Nova builds it, opens it and{" "}
          <span className="text-aurora" data-shimmer="text">
            checks it.
          </span>
        </>
      }
      subtitle="The Agent's Computer is a working environment, not a chat window. Files, terminal, editor, browser, activity and review stay visible while the agent works."
    >
      <div className="grid items-center gap-10 lg:grid-cols-[1.5fr_1fr]">
        <figure>
          <div className="overflow-hidden rounded-2xl border border-white/10 bg-white/[0.03] p-2 shadow-2xl shadow-black/50">
            <Image
              src="/images/nova-workspace.png"
              alt="Nova's Agent's Computer showing the file tree, terminal, editor and browser panels while an app is built and verified"
              width={1920}
              height={1080}
              className="h-auto w-full rounded-xl border border-white/5"
            />
          </div>
          <figcaption className="mt-4 text-xs text-slate-400">
            Live product: Nova built this tip calculator from a single prompt
            and is testing it in its own browser.
          </figcaption>
        </figure>

        <ul className="space-y-5">
          {POINTS.map((point) => (
            <li key={point} className="flex items-start gap-3">
              <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border border-white/15 bg-white/[0.04]">
                <CheckIcon
                  className="size-3.5 text-[#5eead4]"
                  aria-hidden="true"
                />
              </span>
              <p className="text-sm leading-relaxed text-slate-300">{point}</p>
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}
