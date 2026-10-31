"use client";

import { ArrowRightIcon, CheckIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { FlickeringGrid } from "@/components/ui/flickering-grid";
import Galaxy from "@/components/ui/galaxy";
import {
  AnimatedSpan,
  Terminal,
  TypingAnimation,
} from "@/components/ui/terminal";
import { ThreeBackground } from "@/components/ui/three-background";
import { WordRotate } from "@/components/ui/word-rotate";
import { cn } from "@/lib/utils";

/** The rotating verb in the headline. Short enough to read at any width. */
const VERBS = [
  "Build web apps",
  "Audit a codebase",
  "Fix a security hole",
  "Verify a release",
  "Ship software",
];

/** The five steps of the production loop, as the console reports them. */
const LOOP = [
  { step: "ask", line: "the request is queued" },
  { step: "build", line: "a lead agent plans, sub-agents work in sandboxes" },
  { step: "verify", line: "chromium opened the app, 0 console errors" },
  { step: "secure", line: "0 secrets, 0 critical CVEs, gate pass" },
  { step: "ship", line: "build attached with the full audit trail" },
] as const;

const TRUST = [
  "Runs on your hardware",
  "40 security tools in every sandbox",
  "Any model, cloud or local",
];

export function Hero({ className }: { className?: string }) {
  return (
    <section
      className={cn(
        "relative isolate flex min-h-[100svh] w-full items-center overflow-hidden pt-28 pb-16",
        className,
      )}
    >
      {/* Deep space: a slow three-dimensional drift, the old WebGL starfield
          and the flickering grid, each behind the content. */}
      <div
        className="pointer-events-none absolute inset-0 -z-20"
        aria-hidden="true"
      >
        <ThreeBackground intensity={0.45} speed={0.3} />
      </div>
      <div
        className="pointer-events-none absolute inset-0 -z-10"
        aria-hidden="true"
      >
        <Galaxy
          mouseRepulsion={false}
          starSpeed={0.2}
          density={0.6}
          glowIntensity={0.35}
          twinkleIntensity={0.3}
          speed={0.5}
        />
        {/* The old landing dimmed the starfield so the words stay legible. */}
        <div className="absolute inset-0 bg-black/45" />
      </div>
      {/* The grid is the old landing's texture. On a phone it is masked away
          from the middle so the copy sits on a clean surface. */}
      <div className="pointer-events-none absolute inset-0 z-0 [mask-image:radial-gradient(120%_70%_at_50%_35%,transparent_18%,black_78%)] lg:[mask-image:none]">
        <FlickeringGrid
          className="absolute inset-0 translate-y-8"
          squareSize={4}
          gridGap={4}
          color="white"
          maxOpacity={0.3}
          flickerChance={0.25}
        />
      </div>
      {/* Fade the grid into the page so the seam is never visible. */}
      <div
        className="pointer-events-none absolute inset-x-0 bottom-0 z-0 h-64 bg-gradient-to-b from-transparent to-[#0a0a0a]"
        aria-hidden="true"
      />
      {/* A soft pool of shade under the headline column. */}
      <div
        className="pointer-events-none absolute inset-y-0 left-0 z-0 w-full bg-gradient-to-r from-[#0a0a0a]/85 via-[#0a0a0a]/45 to-transparent lg:w-3/5"
        aria-hidden="true"
      />

      <div className="relative z-10 mx-auto grid w-full max-w-6xl gap-14 px-4 md:px-6 lg:grid-cols-[1.05fr_0.95fr] lg:items-center lg:gap-16">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-[#0a0a0a]/70 px-3 py-1 text-[11px] font-medium tracking-[0.18em] text-slate-300 uppercase backdrop-blur-sm">
            <span
              className="size-1.5 rounded-full bg-[#5eead4]"
              aria-hidden="true"
            />
            Open core · self-hosted · MIT licensed
          </span>

          <h1 className="mt-7 max-w-3xl text-4xl font-semibold tracking-tight text-balance text-white sm:text-5xl lg:text-6xl lg:leading-[1.05]">
            The AI developer for companies that{" "}
            <span className="text-shimmer" data-shimmer="text">
              can&apos;t send their code to the cloud.
            </span>
          </h1>

          {/* The rotating verb is the old landing's signature; it now sits
              under the promise as a capability line, not above it. */}
          <div
            className="landing-word-rotate mt-6 flex items-baseline gap-3 text-left"
            aria-hidden="true"
          >
            <span className="font-mono text-xs tracking-[0.16em] text-slate-500 uppercase">
              It can
            </span>
            <WordRotate
              words={VERBS}
              duration={2600}
              className="text-2xl font-semibold tracking-tight sm:text-3xl"
            />
          </div>

          <p className="mt-6 max-w-xl text-base leading-relaxed text-slate-400 sm:text-lg">
            Nova runs on your own servers. It builds web apps, tests them in a
            real browser and checks them for security holes before they go live.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
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
              <Link href="/saas">View pricing</Link>
            </Button>
          </div>

          <ul
            aria-label="Platform highlights"
            className="mt-8 flex flex-wrap gap-x-6 gap-y-3 text-sm text-slate-400"
          >
            {TRUST.map((item) => (
              <li key={item} className="flex items-center gap-2">
                <CheckIcon
                  className="size-4 text-[#5eead4]"
                  aria-hidden="true"
                />
                {item}
              </li>
            ))}
          </ul>
        </div>

        <figure className="relative">
          <div
            className="bg-brand-gradient pointer-events-none absolute -inset-6 -z-10 rounded-[2.5rem] opacity-[0.07] blur-3xl"
            aria-hidden="true"
          />
          <div
            className="shimmer-sweep overflow-hidden rounded-2xl border border-white/10 bg-[#0a0a0a]/80 shadow-2xl shadow-black/50 backdrop-blur-md"
            data-shimmer="sweep"
          >
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3 font-mono text-[11px] text-slate-500">
              <span>nova / production loop</span>
              <span className="flex items-center gap-1.5 text-[#5eead4]">
                <span className="size-1.5 rounded-full bg-[#34d399] motion-safe:animate-pulse" />
                live
              </span>
            </div>

            <Terminal className="h-auto max-h-none w-full max-w-none rounded-none border-0 bg-transparent">
              <TypingAnimation
                className="font-mono text-xs text-slate-400"
                duration={45}
                delay={200}
              >
                {`$ nova run --secure "customer portal"`}
              </TypingAnimation>
              {LOOP.map((row) => (
                <AnimatedSpan
                  key={row.step}
                  className="font-mono text-xs leading-relaxed text-slate-300"
                >
                  <span className="flex items-baseline gap-3">
                    <span className="text-brand-gradient">{row.step}</span>
                    <span className="text-slate-600">|</span>
                    {row.line}
                  </span>
                </AnimatedSpan>
              ))}
            </Terminal>

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/10 px-4 py-3 font-mono text-[11px] text-slate-500">
              <span>40 security tools in every sandbox</span>
              <span>secure-ship gate: ready</span>
              <span>every command logged</span>
            </div>
          </div>
          <figcaption className="mt-4 text-xs text-slate-500">
            A build only ships after the browser verify and the security gate
            pass.
          </figcaption>
        </figure>
      </div>
    </section>
  );
}
