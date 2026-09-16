import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";

/**
 * Root 404. Catches any unmatched route (and manual notFound() calls) with a
 * branded page instead of the framework default.
 */
export default function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Empty className="max-w-md border">
        <EmptyHeader>
          <EmptyTitle>Page not found</EmptyTitle>
          <EmptyDescription>
            The page you’re looking for doesn’t exist or may have moved.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button asChild>
            <Link href="/workspace">Back to workspace</Link>
          </Button>
        </EmptyContent>
      </Empty>
    </div>
  );
}
