import { Skeleton } from "@/components/ui/skeleton";

/** Workspace-scoped suspense fallback: approximates the two-pane layout. */
export default function WorkspaceLoading() {
  return (
    <div
      className="flex h-screen w-full gap-4 p-4"
      role="status"
      aria-busy="true"
      aria-label="Loading workspace"
    >
      <div className="hidden w-64 shrink-0 flex-col gap-3 md:flex">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-6 w-36" />
      </div>
      <div className="flex flex-1 flex-col gap-4">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-5/6" />
        <div className="mt-auto">
          <Skeleton className="h-12 w-full" />
        </div>
      </div>
    </div>
  );
}
