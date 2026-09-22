import { Skeleton } from "@/components/ui/skeleton";

/**
 * Root App Router suspense fallback. Shown while any route segment that lacks
 * its own loading.tsx resolves, replacing the previous blank/frozen paint.
 */
export default function RootLoading() {
  return (
    <div
      className="flex min-h-screen flex-col gap-4 p-6"
      role="status"
      aria-busy="true"
      aria-label="Loading"
    >
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-4 w-full max-w-2xl" />
      <Skeleton className="h-4 w-full max-w-xl" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}
