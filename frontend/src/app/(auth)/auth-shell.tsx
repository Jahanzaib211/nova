"use client";

import { useTheme } from "next-themes";
import type { ReactNode } from "react";

import { FlickeringGrid } from "@/components/ui/flickering-grid";

/** Shared visual shell for the (auth) group login-style pages. */
export function AuthShell({ children }: { children: ReactNode }) {
  const { theme, resolvedTheme } = useTheme();
  const actualTheme = theme === "system" ? resolvedTheme : theme;

  return (
    <div className="bg-background relative flex min-h-screen items-center justify-center overflow-x-hidden overflow-y-auto">
      <FlickeringGrid
        className="absolute inset-0 z-0 mask-[url(/images/nova.svg)] mask-size-[100vw] mask-center mask-no-repeat md:mask-size-[72vh]"
        squareSize={4}
        gridGap={4}
        color={actualTheme === "dark" ? "white" : "black"}
        maxOpacity={0.3}
        flickerChance={0.25}
      />
      <div className="border-border/20 bg-background/5 w-full max-w-md space-y-6 rounded-3xl border p-8 backdrop-blur-sm">
        {children}
      </div>
    </div>
  );
}