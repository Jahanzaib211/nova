import { isStaticWebsiteOnly } from "@/core/static-mode";
import { STARTER_THREAD_IDS } from "@/core/threads/static-threads";

import { ChatProviders } from "./providers";

export function generateStaticParams() {
  if (!isStaticWebsiteOnly()) {
    return [];
  }
  return STARTER_THREAD_IDS.map((thread_id) => ({ thread_id }));
}

export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <ChatProviders>{children}</ChatProviders>;
}
