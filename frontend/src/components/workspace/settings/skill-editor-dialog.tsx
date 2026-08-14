"use client";

import { HistoryIcon, Loader2Icon, SaveIcon, Trash2Icon, Undo2Icon } from "lucide-react";
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
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import {
  useCustomSkill,
  useCustomSkillHistory,
  useDeleteCustomSkill,
  useRollbackCustomSkill,
  useUpdateCustomSkill,
} from "@/core/skills/hooks";
import { formatTimeAgo } from "@/core/utils/datetime";
import { cn } from "@/lib/utils";

function extractMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function formatAction(action: unknown): string {
  if (typeof action !== "string") return "change";
  switch (action) {
    case "human_edit":
      return "edited";
    case "human_delete":
      return "deleted";
    case "rollback":
      return "rolled back";
    case "ai_generated":
      return "AI-generated";
    default:
      return action;
  }
}

export function SkillEditorDialog({
  skillName,
  onOpenChange,
}: {
  skillName: string | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const open = skillName !== null;
  const { data: skill, isLoading, error } = useCustomSkill(skillName);
  const { data: history = [] } = useCustomSkillHistory(skillName);
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [rollbackIndex, setRollbackIndex] = useState<number | null>(null);

  const updateSkill = useUpdateCustomSkill();
  const deleteSkill = useDeleteCustomSkill();
  const rollback = useRollbackCustomSkill();

  useEffect(() => {
    if (open) {
      setContent(skill?.content ?? "");
      setDirty(false);
      setConfirmDelete(false);
      setRollbackIndex(null);
    }
  }, [open, skill?.content]);

  const handleSave = () => {
    if (!skillName) return;
    updateSkill.mutate(
      { skillName, content },
      {
        onSuccess: () => {
          setDirty(false);
          toast.success("Skill saved.");
        },
        onError: (err) => {
          toast.error(extractMessage(err, t.settings.skills.updateError));
        },
      },
    );
  };

  const handleDelete = () => {
    if (!skillName) return;
    deleteSkill.mutate(skillName, {
      onSuccess: () => {
        toast.success("Skill deleted.");
        onOpenChange(false);
      },
      onError: (err) => {
        toast.error(extractMessage(err, "Failed to delete skill."));
      },
    });
  };

  const handleRollback = (index: number) => {
    if (!skillName) return;
    rollback.mutate(
      { skillName, historyIndex: index },
      {
        onSuccess: (result) => {
          setContent(result.content);
          setDirty(false);
          setRollbackIndex(null);
          toast.success("Revision restored.");
        },
        onError: (err) => {
          toast.error(extractMessage(err, "Rollback failed."));
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onOpenChange(false))}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t.settings.skills.editTitle}: {skillName}</DialogTitle>
          <DialogDescription>{t.settings.skills.editorHint}</DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2Icon className="size-4 animate-spin" />
            {t.common.loading}
          </div>
        ) : error ? (
          <div className="py-4 text-sm text-destructive">
            {extractMessage(error, t.settings.skills.loadError)}
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <Textarea
              value={content}
              onChange={(e) => {
                setContent(e.target.value);
                setDirty(true);
              }}
              rows={16}
              className="font-mono text-xs leading-relaxed"
              spellCheck={false}
              placeholder={"---\nname: my-skill\ndescription: ...\nenabled: true\n---\n\n## Instructions\n"}
            />

            <div>
              <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                <HistoryIcon className="size-4" />
                {t.settings.skills.historyTitle}
                <span className="text-xs font-normal text-muted-foreground">({history.length})</span>
              </div>
              {history.length === 0 ? (
                <p className="text-xs text-muted-foreground">{t.settings.skills.historyEmpty}</p>
              ) : (
                <ScrollArea className="max-h-56">
                  <ul className="space-y-1.5 pr-2">
                    {history.map((entry, index) => {
                      const ts = typeof entry.ts === "string" ? entry.ts : null;
                      const action = formatAction(entry.action);
                      const reason =
                        entry.scanner && typeof entry.scanner === "object"
                          ? (entry.scanner as { reason?: string }).reason
                          : undefined;
                      return (
                        <li
                          key={index}
                          className="flex items-center justify-between gap-3 rounded-lg border p-2 text-xs"
                        >
                          <div className="min-w-0">
                            <span className="font-medium">{action}</span>
                            {ts && (
                              <span className="ml-2 text-muted-foreground" title={ts}>
                                {formatTimeAgo(ts)}
                              </span>
                            )}
                            {reason && (
                              <span className="block truncate text-[11px] text-muted-foreground">
                                {reason}
                              </span>
                            )}
                          </div>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={rollback.isPending}
                            onClick={() => setRollbackIndex(index)}
                          >
                            <Undo2Icon className="size-3.5" />
                            {t.settings.skills.historyAction}
                          </Button>
                        </li>
                      );
                    })}
                  </ul>
                </ScrollArea>
              )}
            </div>

            {rollbackIndex !== null && (
              <Dialog
                open
                onOpenChange={(next) => !next && setRollbackIndex(null)}
              >
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>{t.settings.skills.historyRollbackTitle}</DialogTitle>
                    <DialogDescription>
                      {t.settings.skills.historyRollbackDescription}
                    </DialogDescription>
                  </DialogHeader>
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setRollbackIndex(null)}>
                      {t.common.cancel}
                    </Button>
                    <Button
                      variant="destructive"
                      disabled={rollback.isPending}
                      onClick={() => handleRollback(rollbackIndex)}
                    >
                      <Undo2Icon className="size-3.5" />
                      {t.settings.skills.historyRollbackConfirm}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            )}

            {confirmDelete && (
              <Dialog open onOpenChange={(next) => !next && setConfirmDelete(false)}>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>{t.settings.skills.deleteTitle}</DialogTitle>
                    <DialogDescription>
                      {t.settings.skills.deleteDescription}
                    </DialogDescription>
                  </DialogHeader>
                  <DialogFooter>
                    <Button
                      variant="outline"
                      onClick={() => setConfirmDelete(false)}
                      disabled={deleteSkill.isPending}
                    >
                      {t.common.cancel}
                    </Button>
                    <Button
                      variant="destructive"
                      disabled={deleteSkill.isPending}
                      onClick={handleDelete}
                    >
                      <Trash2Icon className="size-3.5" />
                      {t.settings.skills.deleteConfirm}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            )}
          </div>
        )}

        <Separator />
        <DialogFooter className="flex items-center justify-between gap-2">
          <Button
            variant="ghost"
            className={cn("text-destructive", "hover:text-destructive")}
            onClick={() => setConfirmDelete(true)}
            disabled={isLoading || deleteSkill.isPending}
          >
            <Trash2Icon className="size-4" />
            {t.settings.skills.deleteButton}
          </Button>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t.common.cancel}
            </Button>
            <Button
              disabled={!dirty || isLoading || updateSkill.isPending}
              onClick={handleSave}
            >
              {updateSkill.isPending ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <SaveIcon className="size-4" />
              )}
              {t.settings.skills.saveButton}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}