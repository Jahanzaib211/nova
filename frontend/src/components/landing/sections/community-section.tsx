"use client";

import { GitHubLogoIcon } from "@radix-ui/react-icons";
import Link from "next/link";

import { AuroraText } from "@/components/ui/aurora-text";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";

import { Section } from "../section";

export function CommunitySection() {
  const { t } = useI18n();
  return (
    <Section
      title={
        <AuroraText colors={["#60A5FA", "#A5FA60", "#A560FA"]}>
          Join the Community
        </AuroraText>
      }
      subtitle={t.landing.community.subtitle}
    >
      <div className="flex justify-center">
        <Button className="text-xl" size="lg" asChild>
          <Link
            href="https://github.com/Jahanzaib211/nova"
            target="_blank"
            rel="noopener noreferrer"
          >
            <GitHubLogoIcon />
            Contribute Now
          </Link>
        </Button>
      </div>
    </Section>
  );
}
