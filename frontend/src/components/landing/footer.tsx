"use client";

import Link from "next/link";
import { useMemo } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export type FooterProps = {
  className?: string;
};

export function Footer({ className }: FooterProps) {
  const { t } = useI18n();
  const year = useMemo(() => new Date().getFullYear(), []);
  return (
    <footer
      className={cn(
        "mx-auto mt-8 w-full max-w-6xl px-4 pb-12 md:px-6",
        className,
      )}
    >
      <hr className="m-0 h-px w-full border-none bg-gradient-to-r from-transparent via-white/15 to-transparent" />
      <div className="flex flex-col items-center justify-center py-10 text-center">
        <p className="max-w-xl font-serif text-lg text-balance text-slate-200 md:text-xl">
          &quot;Originated from Open Source, give back to Open Source.&quot;
        </p>
      </div>
      <div className="flex flex-col items-center justify-center gap-3 text-xs text-slate-500">
        <nav className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2">
          <Link href="/saas" className="transition-colors hover:text-white">
            Pricing
          </Link>
          <Link href="/terms" className="transition-colors hover:text-white">
            Terms
          </Link>
          <Link href="/privacy" className="transition-colors hover:text-white">
            Privacy
          </Link>
          <a
            href="https://alilabsx.com/"
            target="_blank"
            rel="noopener noreferrer"
            className="transition-colors hover:text-white"
          >
            AliLabsX
          </a>
        </nav>
        <p>{t.landing.footer.license}</p>
        <p>
          &copy; {year} Nova &middot; Built by{" "}
          <a
            href="https://alilabsx.com/"
            target="_blank"
            rel="noopener noreferrer"
            className="text-slate-400 transition-colors hover:text-[#5eead4]"
          >
            Jahanzaib Ali
          </a>
        </p>
      </div>
    </footer>
  );
}
