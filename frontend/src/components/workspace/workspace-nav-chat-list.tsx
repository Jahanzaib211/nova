"use client";

import { BotIcon, MessagesSquare } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import { useFeatureFlags } from "@/core/runtime/feature-flags";
import { navItems } from "@/features/registry";

export function WorkspaceNavChatList() {
  const { t } = useI18n();
  const pathname = usePathname();
  const flags = useFeatureFlags();
  // Feature modules contribute their own entries (src/features/*/manifest).
  const extra = navItems().filter(
    (item) => item.flag === undefined || flags[item.flag],
  );
  return (
    <SidebarGroup className="pt-1">
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton isActive={pathname === "/workspace/chats"} asChild>
            <Link className="text-muted-foreground" href="/workspace/chats">
              <MessagesSquare />
              <span>{t.sidebar.chats}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/agents")}
            asChild
          >
            <Link className="text-muted-foreground" href="/workspace/agents">
              <BotIcon />
              <span>{t.sidebar.agents}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        {extra.map(({ id, icon: Icon, label, href }) => (
          <SidebarMenuItem key={id}>
            <SidebarMenuButton isActive={pathname.startsWith(href)} asChild>
              <Link className="text-muted-foreground" href={href}>
                <Icon />
                <span>{label(t)}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  );
}
