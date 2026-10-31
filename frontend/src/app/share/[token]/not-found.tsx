import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";

/** Shown when a share token is unknown, revoked, or expired. */
export default function ShareNotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Empty className="max-w-md border">
        <EmptyHeader>
          <EmptyTitle>This shared link isn’t available</EmptyTitle>
          <EmptyDescription>
            The link may have expired, been revoked, or never existed.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button asChild>
            <Link href="/">Go home</Link>
          </Button>
        </EmptyContent>
      </Empty>
    </div>
  );
}
