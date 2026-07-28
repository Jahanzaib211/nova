"use client";

import { GitHubLogoIcon } from "@radix-ui/react-icons";
import { MenuIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

export type MobileNavProps = {
  /** Links are passed in from the server component so it keeps owning i18n. */
  links: { href: string; label: string }[];
  githubURL: string;
  githubLabel: string;
  menuLabel: string;
};

/**
 * Narrow-screen counterpart to the header's inline nav — the desktop row of
 * links plus the GitHub button does not fit under `md`.
 */
export function MobileNav({
  links,
  githubURL,
  githubLabel,
  menuLabel,
}: MobileNavProps) {
  const [open, setOpen] = useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="md:hidden"
          aria-label={menuLabel}
        >
          <MenuIcon className="size-5" />
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-72">
        <SheetTitle className="px-4 pt-4 font-serif text-xl">Nova</SheetTitle>
        <nav className="flex flex-col gap-1 p-4">
          {links.map((link) => (
            <SheetClose key={link.href} asChild>
              <Link
                href={link.href}
                className="text-secondary-foreground hover:text-foreground hover:bg-accent flex min-h-11 items-center rounded-md px-3 text-base font-medium transition-colors"
              >
                {link.label}
              </Link>
            </SheetClose>
          ))}
          <Button variant="outline" asChild className="mt-3 w-full">
            <a href={githubURL} target="_blank" rel="noopener noreferrer">
              <GitHubLogoIcon className="size-4" />
              {githubLabel}
            </a>
          </Button>
        </nav>
      </SheetContent>
    </Sheet>
  );
}
