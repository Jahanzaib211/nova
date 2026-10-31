import Link from "next/link";

import { Card } from "@/components/ui/card";
import { pathOfThread } from "@/core/threads/utils";
import { cn } from "@/lib/utils";

import { Section } from "../section";

/**
 * Starter threads that ship with Nova. They are open-source examples, not
 * customer stories, so the page says exactly that.
 */
const STARTERS = [
  {
    threadId: "7cfa5f8f-a2f8-47ad-acbd-da7137baf990",
    title: "Forecast 2026 agent trends and opportunities",
    description:
      "A deep research report on agent technology trends, turned into a webpage.",
  },
  {
    threadId: "4f3e55ee-f853-43db-bfb3-7d1a411f03cb",
    title: "Generate a video from Pride and Prejudice",
    description:
      "Find the scene in the novel, then generate a video and a reference image from it.",
  },
  {
    threadId: "21cfea46-34bd-4aa6-9e1f-3009452fbeb9",
    title: "Doraemon explains MoE",
    description:
      "A comic strip that explains mixture-of-experts to a teenager.",
  },
  {
    threadId: "ad76c455-5bf9-4335-8517-fc03834ab828",
    title: "Titanic data analysis",
    description:
      "Exploratory data analysis on the Titanic dataset, with the factors behind survival.",
  },
  {
    threadId: "d3e5adaf-084c-4dd5-9d29-94f1d6bccd98",
    title: "YC video then deep research",
    description:
      "Watch a Y Combinator video, then research the advice in it for founders.",
  },
  {
    threadId: "3823e443-4e2b-4679-b496-a9506eae462b",
    title: "Fei-Fei Li podcast digest",
    description:
      "Collect recent podcast appearances and summarize them into one report.",
  },
];

export function CaseStudySection({ className }: { className?: string }) {
  return (
    <Section
      id="starter-threads"
      className={className}
      kicker="Starter threads"
      title="Try Nova on real tasks"
      subtitle="Built-in starter projects that ship with Nova. Open one in a new tab and watch the loop run."
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {STARTERS.map((starter) => (
          <Link
            key={starter.threadId}
            href={pathOfThread(starter.threadId) + "?mock=true"}
            target="_blank"
            rel="noopener noreferrer"
            className="group/starter block focus-visible:outline-none"
          >
            <Card className="group/starter relative h-64 overflow-hidden border-white/10 bg-white/[0.03]">
              <div
                className="absolute inset-0 z-0 bg-cover bg-center bg-no-repeat transition-all duration-300 group-hover/starter:scale-110 group-hover/starter:brightness-90"
                style={{
                  backgroundImage: `url(/images/${starter.threadId}.jpg)`,
                }}
              />
              <span className="absolute top-4 left-4 z-10 rounded-full border border-white/15 bg-black/60 px-2.5 py-1 text-[10px] font-medium tracking-wide text-slate-300 backdrop-blur-sm">
                Starter project
              </span>
              <div
                className={cn(
                  "flex h-full w-full translate-y-[calc(100%-56px)] flex-col items-center",
                  "transition-all duration-300",
                  "group-hover/starter:translate-y-[calc(100%-128px)]",
                  // No :hover on touch — show the description up front instead
                  // of leaving it permanently out of reach.
                  "max-md:translate-y-[calc(100%-128px)]",
                )}
              >
                <div
                  className="flex w-full flex-col p-4"
                  style={{
                    background:
                      "linear-gradient(to bottom, rgba(0, 0, 0, 0) 0%, rgba(0, 0, 0, 1) 100%)",
                  }}
                >
                  <div className="flex flex-col gap-2">
                    <h3 className="flex h-14 items-center text-xl font-bold text-shadow-black">
                      {starter.title}
                    </h3>
                    <p className="box-shadow-black overflow-hidden text-sm text-white/85 text-shadow-black">
                      {starter.description}
                    </p>
                  </div>
                </div>
              </div>
            </Card>
          </Link>
        ))}
      </div>
    </Section>
  );
}
