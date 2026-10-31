import { Skeleton } from "@/components/ui/skeleton";

/** Per-thread suspense fallback: a few message-shaped placeholders. */
export default function ThreadLoading() {
  return (
    <div
      className="flex flex-1 flex-col gap-6 p-6"
      role="status"
      aria-busy="true"
      aria-label="Loading conversation"
    >
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex flex-col gap-2">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-16 w-full max-w-2xl" />
        </div>
      ))}
    </div>
  );
}
