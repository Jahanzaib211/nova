"use client";

import { Link2, Loader2, MessageSquare } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo } from "react";

import { QueryClientProvider } from "@/components/query-client-provider";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useI18n } from "@/core/i18n/hooks";
import { useSharedThread } from "@/core/sharing/hooks";
import { formatTimeAgo } from "@/core/utils/datetime";

function renderContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block;
        if (block && typeof block === "object") {
          const text = (block as { text?: unknown }).text;
          if (typeof text === "string") return text;
          if (text && typeof text === "object") return renderContent(text);
        }
        return JSON.stringify(block);
      })
      .join("\n");
  }
  if (content && typeof content === "object") {
    const inner = (content as { content?: unknown }).content;
    if (Array.isArray(inner)) return renderContent(inner);
    if (typeof inner === "string") return inner;
  }
  return JSON.stringify(content, null, 2);
}

function SharedThreadPageInner() {
  const { t } = useI18n();
  const params = useParams<{ token: string }>();
  const token = params.token ?? "";
  const { data, isLoading, isError } = useSharedThread(token);

  const messages = useMemo(
    () =>
      (data?.messages ?? []).filter(
        (message) => renderContent(message.content).trim() !== "",
      ),
    [data],
  );

  if (isLoading) {
    return (
      <div className="text-muted-foreground flex min-h-screen items-center justify-center bg-[#0a0a0a]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[#0a0a0a] p-6 text-center">
        <Link2 className="text-muted-foreground h-10 w-10" />
        <h1 className="text-xl font-semibold">{t.sharePage.notFound}</h1>
        <p className="text-muted-foreground text-sm">
          {t.sharePage.notFoundHint}
        </p>
        <Link href="/">
          <Button variant="outline" size="sm">
            {t.sharePage.backHome}
          </Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] py-10">
      <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4">
        <div className="flex items-center justify-between gap-4">
          <h1 className="truncate text-lg font-semibold">
            {data.thread_title ?? t.common.thinking}
          </h1>
          <Link href="/">
            <Button variant="ghost" size="sm">
              {t.sharePage.backHome}
            </Button>
          </Link>
        </div>
        {messages.length === 0 ? (
          <Card>
            <CardContent className="text-muted-foreground flex flex-col items-center gap-2 py-12">
              <MessageSquare className="h-8 w-8" />
              <p className="text-sm">{t.sharePage.messagesEmpty}</p>
            </CardContent>
          </Card>
        ) : (
          messages.map((message, index) => {
            const isHuman = message.event_type === "human_message";
            const text = renderContent(message.content);
            return (
              <Card
                key={`${message.seq}-${index}`}
                className={isHuman ? "border-border/50" : "bg-card/60"}
              >
                <CardHeader className="flex flex-row items-center gap-2 pb-2">
                  <CardTitle className="text-sm">
                    {isHuman ? "You" : "Nova"}
                  </CardTitle>
                  <CardDescription className="ml-auto text-xs">
                    {message.created_at
                      ? formatTimeAgo(message.created_at)
                      : ""}
                  </CardDescription>
                </CardHeader>
                <CardContent
                  className={
                    "text-sm leading-relaxed break-words whitespace-pre-wrap " +
                    (isHuman ? "text-foreground" : "text-muted-foreground")
                  }
                >
                  {text}
                </CardContent>
              </Card>
            );
          })
        )}
      </div>
    </div>
  );
}

export default function SharedThreadPage() {
  return (
    <QueryClientProvider>
      <SharedThreadPageInner />
    </QueryClientProvider>
  );
}
