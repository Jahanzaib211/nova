import { GitHubLogoIcon } from "@radix-ui/react-icons";
import { ArrowRightIcon, MailIcon } from "lucide-react";
import Link from "next/link";

import { AuroraText } from "@/components/ui/aurora-text";
import { Button } from "@/components/ui/button";

import { Section } from "../section";

export function CommunitySection({ className }: { className?: string }) {
  return (
    <Section
      id="get-started"
      className={className}
      kicker="Get started"
      title={
        <AuroraText colors={["#926dff", "#22d3ee"]} speed={0.6}>
          Run Nova on your hardware.
        </AuroraText>
      }
      subtitle="Start with the free edition, run the code yourself, or talk to us about a pilot. Enterprise installs are sold, deployed and supported with our partner."
    >
      <div className="flex flex-wrap items-center gap-3">
        <Button
          asChild
          size="lg"
          className="h-11 rounded-lg px-6 text-sm font-semibold"
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
          <a
            href="https://github.com/Jahanzaib211/nova"
            target="_blank"
            rel="noopener noreferrer"
          >
            <GitHubLogoIcon className="size-4" aria-hidden="true" />
            Get the code
          </a>
        </Button>
        <Button
          asChild
          size="lg"
          variant="outline"
          className="h-11 rounded-lg border-white/15 bg-transparent px-6 text-sm font-semibold text-white hover:border-white/30 hover:bg-white/5 hover:text-white"
        >
          <a href="mailto:alilabsx@gmail.com">
            <MailIcon className="size-4" aria-hidden="true" />
            Talk to us
          </a>
        </Button>
      </div>
    </Section>
  );
}
