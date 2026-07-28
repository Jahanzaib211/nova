import "@/styles/globals.css";
import "katex/dist/katex.min.css";

import { type Metadata, type Viewport } from "next";

import { ErrorBoundary } from "@/components/error-boundary";
import { ThemeProvider } from "@/components/theme-provider";
import { I18nProvider } from "@/core/i18n/context";
import { detectLocaleServer } from "@/core/i18n/server";

export const metadata: Metadata = {
  title: "Nova — The Agent's Computer",
  description:
    "Nova is an enterprise-grade agent platform that spins up fast, does the work, ships the build, and fades. Built on LangGraph + LangChain.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // The workspace runs edge to edge, so let it paint under the notch/home
  // indicator; `safe-area-inset-*` then handles the padding.
  viewportFit: "cover",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const locale = await detectLocaleServer();
  return (
    <html lang={locale} suppressContentEditableWarning suppressHydrationWarning>
      <body>
        <ThemeProvider attribute="class" enableSystem disableTransitionOnChange>
          <I18nProvider initialLocale={locale}>
            <ErrorBoundary scope="root">{children}</ErrorBoundary>
          </I18nProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
