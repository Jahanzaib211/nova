"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";

/**
 * Route-level error boundary for any segment without its own error.tsx.
 * Recovers in place via reset() instead of falling through to the fatal
 * global-error document. Fatal (layout/provider) failures still escape to
 * app/global-error.tsx.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surface to the console/observability; the digest correlates with the
    // server log for this render.
    console.error("Route render error:", error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center p-6">
      <Empty className="max-w-md border">
        <EmptyHeader>
          <EmptyTitle>Something went wrong</EmptyTitle>
          <EmptyDescription>
            This section failed to render. You can retry without reloading the
            whole app.
            {error.digest ? (
              <code className="mt-3 block text-xs opacity-70">
                ref: {error.digest}
              </code>
            ) : null}
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button onClick={() => reset()}>Try again</Button>
        </EmptyContent>
      </Empty>
    </div>
  );
}
