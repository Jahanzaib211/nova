"use client";

import Link from "next/link";

import {
  Sidebar,
  SidebarHeader,
  SidebarContent,
  SidebarFooter,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";

import { WorkspaceChannelsList } from "./channels/workspace-channels-list";
import { RecentChatList } from "./recent-chat-list";
import { WorkspaceHeader } from "./workspace-header";
import { WorkspaceNavChatList } from "./workspace-nav-chat-list";
import { WorkspaceNavMenu } from "./workspace-nav-menu";

export function WorkspaceSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const { open: isSidebarOpen } = useSidebar();
  return (
    <>
      <Sidebar variant="sidebar" collapsible="icon" {...props}>
        <SidebarHeader className="py-0">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent>
          <WorkspaceNavChatList />
          <WorkspaceChannelsList />
          {isSidebarOpen && <RecentChatList />}
        </SidebarContent>
        <SidebarFooter>
          <WorkspaceNavMenu />
          {isSidebarOpen && (
            <Link
              href="/saas"
              className="text-muted-foreground/60 hover:text-muted-foreground cursor-pointer px-2 pb-1 text-center text-[10px] tracking-wide transition"
            >
              Made by Ali Technologies
            </Link>
          )}
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </>
  );
}
