"use client";

import { ShieldAlertIcon } from "lucide-react";

import { formatAttention, useThreadBrowserState } from "@/core/browser/api";
import { useI18n } from "@/core/i18n/hooks";

/**
 * Shown when a browser tool or the web_fetch ladder hit a captcha: the agent
 * is waiting (browser_await_human) for the user to solve it in the noVNC live
 * view. "Done" tells the agent to continue; it also continues on its own once
 * the challenge disappears from the page. Nova never sends captchas to a
 * third-party solver.
 */
export function BrowserAttentionBanner({
  threadId,
  active,
  onOpenLiveView,
}: {
  threadId: string;
  active: boolean;
  onOpenLiveView?: () => void;
}) {
  const { t } = useI18n();
  const tb = t.features.browser;
  const { data, resolve } = useThreadBrowserState(threadId, active);
  const att = data?.attention;
  if (!att || att.resolved_at) return null;
  return (
    <div
      role="alert"
      data-testid="browser-attention"
      className="border-warning/40 bg-warning/10 flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2 text-xs"
    >
      <ShieldAlertIcon className="text-warning h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{tb.attentionTitle}</div>
        <div className="text-muted-foreground truncate">
          {formatAttention(tb.attentionBody, att)}
        </div>
      </div>
      {onOpenLiveView && (
        <button
          type="button"
          onClick={onOpenLiveView}
          className="border-panel-border hover:bg-muted/30 rounded border px-2 py-1"
        >
          {tb.openLiveView}
        </button>
      )}
      <button
        type="button"
        onClick={() => void resolve()}
        className="bg-primary text-primary-foreground rounded px-2 py-1"
      >
        {tb.done}
      </button>
    </div>
  );
}
