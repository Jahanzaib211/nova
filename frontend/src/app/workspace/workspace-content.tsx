import { cookies } from "next/headers";
import { Toaster } from "sonner";

import { QueryClientProvider } from "@/components/query-client-provider";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { AuthSuccessToast } from "@/components/workspace/auth-success-toast";
import { CommandPalette } from "@/components/workspace/command-palette";
import { GatewayOfflineBanner } from "@/components/workspace/gateway-offline-banner";
import { WorkspaceSidebar } from "@/components/workspace/workspace-sidebar";

function parseSidebarOpenCookie(
  value: string | undefined,
): boolean | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

export async function WorkspaceContent({
  children,
  gatewayUnavailable = false,
}: Readonly<{
  children: React.ReactNode;
  gatewayUnavailable?: boolean;
}>) {
  const cookieStore = await cookies();
  const initialSidebarOpen = parseSidebarOpenCookie(
    cookieStore.get("sidebar_state")?.value,
  );

  return (
    <QueryClientProvider>
      <SidebarProvider className="h-screen" defaultOpen={initialSidebarOpen}>
        <WorkspaceSidebar />
        <SidebarInset className="min-w-0">
          <GatewayOfflineBanner gatewayUnavailable={gatewayUnavailable} />
          {children}
        </SidebarInset>
      </SidebarProvider>
      <CommandPalette />
      <Toaster position="top-center" />
      {/* Must mount after Toaster: sibling effects run in tree order, and the
          toast fired here is dropped unless sonner is already subscribed. */}
      <AuthSuccessToast />
    </QueryClientProvider>
  );
}
