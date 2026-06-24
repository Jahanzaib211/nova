"use client";

import {
  DownloadIcon,
  EyeIcon,
  FolderOpenIcon,
  KeyboardIcon,
  MessageSquarePlusIcon,
  SearchIcon,
  SettingsIcon,
  TerminalIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";
import { useGlobalShortcuts } from "@/hooks/use-global-shortcuts";
import { usePanels } from "@/components/workspace/panels/context";

import { SettingsDialog } from "./settings";

export function CommandPalette() {
  const { t } = useI18n();
  const router = useRouter();
  const { agentComputerOpen, setAgentComputerOpen } = usePanels();
  const [open, setOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [isMac, setIsMac] = useState(false);

  const handleNewChat = useCallback(() => {
    router.push("/workspace/chats/new");
    setOpen(false);
  }, [router]);

  const handleOpenSettings = useCallback(() => {
    setOpen(false);
    setSettingsOpen(true);
  }, []);

  const handleShowShortcuts = useCallback(() => {
    setOpen(false);
    setShortcutsOpen(true);
  }, []);

  const shortcuts = useMemo(
    () => [
      { key: "k", meta: true, action: () => setOpen((o) => !o) },
      { key: "n", meta: true, shift: true, action: handleNewChat },
      { key: ",", meta: true, action: handleOpenSettings },
      { key: "/", meta: true, action: handleShowShortcuts },
    ],
    [handleNewChat, handleOpenSettings, handleShowShortcuts],
  );

  useGlobalShortcuts(shortcuts);

  useEffect(() => {
    setIsMac(navigator.userAgent.includes("Mac"));
  }, []);
  const metaKey = isMac ? "⌘" : "Ctrl+";
  const shiftKey = isMac ? "⇧" : "Shift+";

  return (
    <>
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
      <CommandDialog open={open} onOpenChange={setOpen}>
        <CommandInput placeholder={t.shortcuts.searchActions} />
        <CommandList>
          <CommandEmpty>{t.shortcuts.noResults}</CommandEmpty>
          <CommandGroup heading={t.shortcuts.actions}>
            <CommandItem onSelect={handleNewChat}>
              <MessageSquarePlusIcon className="mr-2 h-4 w-4" />
              {t.sidebar.newChat}
              <CommandShortcut>
                {metaKey}
                {shiftKey}N
              </CommandShortcut>
            </CommandItem>
            <CommandItem onSelect={handleOpenSettings}>
              <SettingsIcon className="mr-2 h-4 w-4" />
              {t.common.settings}
              <CommandShortcut>{metaKey},</CommandShortcut>
            </CommandItem>
            <CommandItem onSelect={handleShowShortcuts}>
              <KeyboardIcon className="mr-2 h-4 w-4" />
              {t.shortcuts.keyboardShortcuts}
              <CommandShortcut>{metaKey}/</CommandShortcut>
            </CommandItem>
          </CommandGroup>

          <CommandGroup heading="Agent's Computer">
            <CommandItem
              onSelect={() => {
                setAgentComputerOpen(!agentComputerOpen);
                setOpen(false);
              }}
            >
              <TerminalIcon className="mr-2 h-4 w-4" />
              {agentComputerOpen ? "Close" : "Open"} Agent&apos;s Computer
            </CommandItem>
            <CommandItem
              onSelect={() => {
                setAgentComputerOpen(true);
                setOpen(false);
              }}
            >
              <EyeIcon className="mr-2 h-4 w-4" />
              Preview built file — open right panel → Preview tab
            </CommandItem>
            <CommandItem
              onSelect={() => {
                setAgentComputerOpen(true);
                setOpen(false);
              }}
            >
              <FolderOpenIcon className="mr-2 h-4 w-4" />
              Browse workspace files — open right panel → Files tab
            </CommandItem>
          </CommandGroup>

          <CommandGroup heading="Agent Capabilities (type in chat)">
            <CommandItem onSelect={() => setOpen(false)}>
              <SearchIcon className="mr-2 h-4 w-4" />
              search_files — find files by pattern (e.g. &quot;search for *.html files&quot;)
            </CommandItem>
            <CommandItem onSelect={() => setOpen(false)}>
              <SearchIcon className="mr-2 h-4 w-4" />
              grep_files — search file contents (e.g. &quot;grep for nav class in workspace&quot;)
            </CommandItem>
            <CommandItem onSelect={() => setOpen(false)}>
              <DownloadIcon className="mr-2 h-4 w-4" />
              Download — click ↓ button in Agent&apos;s Computer header when files exist
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </CommandDialog>

      <Dialog open={shortcutsOpen} onOpenChange={setShortcutsOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t.shortcuts.keyboardShortcuts}</DialogTitle>
            <DialogDescription>
              {t.shortcuts.keyboardShortcutsDescription}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            {[
              { keys: `${metaKey}K`, label: t.shortcuts.openCommandPalette },
              { keys: `${metaKey}${shiftKey}N`, label: t.sidebar.newChat },
              { keys: `${metaKey}B`, label: t.shortcuts.toggleSidebar },
              { keys: `${metaKey},`, label: t.common.settings },
              {
                keys: `${metaKey}/`,
                label: t.shortcuts.keyboardShortcuts,
              },
            ].map(({ keys, label }) => (
              <div key={keys} className="flex items-center justify-between">
                <span className="text-muted-foreground">{label}</span>
                <kbd className="bg-muted text-muted-foreground rounded px-2 py-0.5 font-mono text-xs">
                  {keys}
                </kbd>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
