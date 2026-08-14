"use client";

import { Link2, Loader2, Share2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { writeTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";
import { useCreateShareLink, useRevokeShareLink } from "@/core/sharing/hooks";

interface ShareDialogProps {
  threadId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ShareDialog({
  threadId,
  open,
  onOpenChange,
}: ShareDialogProps) {
  const { t } = useI18n();
  const [token, setToken] = useState<string | null>(null);

  const createShareLink = useCreateShareLink();
  const revokeShareLink = useRevokeShareLink();

  useEffect(() => {
    if (!open || threadId === null) {
      return;
    }
    setToken(null);
    createShareLink.mutate(threadId, {
      onSuccess: (link) => setToken(link.token),
      onError: () => {
        toast.error(t.common.shareFailed);
        onOpenChange(false);
      },
    });
  }, [open, threadId, createShareLink, onOpenChange, t]);

  const shareUrl =
    token === null ? null : `${window.location.origin}/share/${token}`;

  const handleCopy = async () => {
    if (shareUrl === null) return;
    const didCopy = await writeTextToClipboard(shareUrl);
    if (!didCopy) {
      toast.error(t.clipboard.failedToCopyToClipboard);
      return;
    }
    toast.success(t.clipboard.linkCopied);
  };

  const handleRevoke = () => {
    if (threadId === null) return;
    revokeShareLink.mutate(threadId, {
      onSuccess: () => {
        setToken(null);
        toast.success(t.common.shareRevoked);
      },
      onError: () => toast.error(t.common.shareFailed),
    });
  };

  const busy = createShareLink.isPending || revokeShareLink.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-[425px]"
        aria-describedby={undefined}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Share2 className="h-4 w-4" />
            {t.common.share}
          </DialogTitle>
          <DialogDescription>{t.common.shareHint}</DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2 py-4">
          <Input readOnly value={shareUrl ?? ""} placeholder={t.common.sharePlaceholder} />
          {token !== null && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => void handleCopy()}
              title={t.common.copyLink}
            >
              <Link2 className="h-4 w-4" />
              <span className="sr-only">{t.common.copyLink}</span>
            </Button>
          )}
        </div>
        <DialogFooter>
          {token !== null && (
            <Button
              variant="destructive"
              onClick={handleRevoke}
              disabled={busy}
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              {t.common.revoke}
            </Button>
          )}
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            {t.common.close}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}