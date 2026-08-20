"use client";

import {
  LoaderCircleIcon,
  PencilIcon,
  PlusIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { useState } from "react";
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
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import { MCPConfigRequestError } from "@/core/mcp/api";
import {
  useCreateMCPServer,
  useDeleteMCPServer,
  useEnableMCPServer,
  useMCPConfig,
  useResetMCPCache,
  useUpdateMCPServer,
} from "@/core/mcp/hooks";
import type { MCPServerConfig } from "@/core/mcp/types";
import { env } from "@/env";

import { SettingsSection } from "./settings-section";

type TransportType = "stdio" | "sse" | "http";

interface KeyValueRow {
  key: string;
  value: string;
}

interface FormState {
  name: string;
  description: string;
  enabled: boolean;
  type: TransportType;
  command: string;
  args: string;
  url: string;
  env: KeyValueRow[];
  headers: KeyValueRow[];
}

function emptyForm(): FormState {
  return {
    name: "",
    description: "",
    enabled: true,
    type: "stdio",
    command: "",
    args: "",
    url: "",
    env: [],
    headers: [],
  };
}

function recordToRows(record?: Record<string, string>): KeyValueRow[] {
  return Object.entries(record ?? {}).map(([key, value]) => ({ key, value }));
}

function rowsToRecord(rows: KeyValueRow[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const row of rows) {
    if (row.key.trim()) {
      result[row.key.trim()] = row.value;
    }
  }
  return result;
}

function formFromServer(name: string, server: MCPServerConfig): FormState {
  return {
    name,
    description: server.description,
    enabled: server.enabled,
    type: server.type ?? "stdio",
    command: server.command ?? "",
    args: (server.args ?? []).join("\n"),
    url: server.url ?? "",
    env: recordToRows(server.env),
    headers: recordToRows(server.headers),
  };
}

function formToServerConfig(form: FormState): MCPServerConfig {
  const base: MCPServerConfig = {
    enabled: form.enabled,
    description: form.description.trim(),
    type: form.type,
    env: rowsToRecord(form.env),
  };
  if (form.type === "stdio") {
    base.command = form.command.trim();
    base.args = form.args
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
  } else {
    base.url = form.url.trim();
    base.headers = rowsToRecord(form.headers);
  }
  return base;
}

function FieldRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3 sm:items-center sm:gap-3">
      <div className="text-sm font-medium">{label}</div>
      <div className="sm:col-span-2">{children}</div>
    </div>
  );
}

function KeyValueEditor({
  rows,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
  addLabel,
  removeLabel,
}: {
  rows: KeyValueRow[];
  onChange: (rows: KeyValueRow[]) => void;
  keyPlaceholder: string;
  valuePlaceholder: string;
  addLabel: string;
  removeLabel: string;
}) {
  const update = (index: number, patch: Partial<KeyValueRow>) => {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };
  const remove = (index: number) => {
    onChange(rows.filter((_, i) => i !== index));
  };
  return (
    <div className="flex flex-col gap-2">
      {rows.map((row, index) => (
        <div key={index} className="flex gap-2">
          <Input
            value={row.key}
            placeholder={keyPlaceholder}
            onChange={(e) => update(index, { key: e.target.value })}
          />
          <Input
            value={row.value}
            placeholder={valuePlaceholder}
            onChange={(e) => update(index, { value: e.target.value })}
          />
          <Button
            variant="outline"
            size="icon"
            aria-label={removeLabel}
            onClick={() => remove(index)}
          >
            <XIcon className="size-4" />
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => onChange([...rows, { key: "", value: "" }])}
      >
        <PlusIcon className="size-4" />
        {addLabel}
      </Button>
    </div>
  );
}

function MCPServerFormDialog({
  open,
  onOpenChange,
  editingName,
  initialForm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editingName: string | null;
  initialForm: FormState;
}) {
  const { t } = useI18n();
  const strings = t.settings.tools;
  const [form, setForm] = useState<FormState>(initialForm);
  const createServer = useCreateMCPServer();
  const updateServer = useUpdateMCPServer();
  const isEdit = editingName !== null;
  const isPending = createServer.isPending || updateServer.isPending;

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const canSubmit =
    form.name.trim().length > 0 &&
    (form.type !== "stdio" || form.command.trim().length > 0) &&
    (form.type === "stdio" || form.url.trim().length > 0);

  const handleSubmit = () => {
    const serverName = form.name.trim();
    const server = formToServerConfig(form);
    const mutation = isEdit
      ? updateServer.mutateAsync({ serverName, server })
      : createServer.mutateAsync({ serverName, server });
    mutation
      .then(() => {
        toast.success(isEdit ? strings.updated : strings.created);
        onOpenChange(false);
      })
      .catch((error: Error) => toast.error(error.message));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? strings.editTitle : strings.addTitle}
          </DialogTitle>
          <DialogDescription>{strings.formDescription}</DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
          <FieldRow label={strings.fieldName}>
            <Input
              value={form.name}
              disabled={isEdit}
              placeholder="my-mcp-server"
              onChange={(e) => set("name", e.target.value)}
            />
          </FieldRow>
          <FieldRow label={strings.fieldDescription}>
            <Input
              value={form.description}
              onChange={(e) => set("description", e.target.value)}
            />
          </FieldRow>
          <FieldRow label={strings.fieldType}>
            <Select
              value={form.type}
              onValueChange={(v) => set("type", v as TransportType)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="stdio">{strings.typeStdio}</SelectItem>
                <SelectItem value="sse">{strings.typeSse}</SelectItem>
                <SelectItem value="http">{strings.typeHttp}</SelectItem>
              </SelectContent>
            </Select>
          </FieldRow>
          {form.type === "stdio" ? (
            <>
              <FieldRow label={strings.fieldCommand}>
                <Input
                  value={form.command}
                  placeholder="npx"
                  onChange={(e) => set("command", e.target.value)}
                />
              </FieldRow>
              <FieldRow label={strings.fieldArgs}>
                <Textarea
                  value={form.args}
                  placeholder={strings.fieldArgsHint}
                  rows={3}
                  onChange={(e) => set("args", e.target.value)}
                />
              </FieldRow>
            </>
          ) : (
            <FieldRow label={strings.fieldUrl}>
              <Input
                value={form.url}
                placeholder="https://example.com/mcp"
                onChange={(e) => set("url", e.target.value)}
              />
            </FieldRow>
          )}
          <FieldRow label={strings.fieldEnv}>
            <KeyValueEditor
              rows={form.env}
              onChange={(rows) => set("env", rows)}
              keyPlaceholder={strings.envKeyPlaceholder}
              valuePlaceholder={strings.envValuePlaceholder}
              addLabel={strings.addEnvVar}
              removeLabel={strings.removeRow}
            />
          </FieldRow>
          {form.type !== "stdio" && (
            <FieldRow label={strings.fieldHeaders}>
              <KeyValueEditor
                rows={form.headers}
                onChange={(rows) => set("headers", rows)}
                keyPlaceholder={strings.envKeyPlaceholder}
                valuePlaceholder={strings.envValuePlaceholder}
                addLabel={strings.addHeader}
                removeLabel={strings.removeRow}
              />
            </FieldRow>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t.common.cancel}
          </Button>
          <Button disabled={!canSubmit || isPending} onClick={handleSubmit}>
            {isPending && <LoaderCircleIcon className="size-4 animate-spin" />}
            {strings.saveButton}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ToolSettingsPage() {
  const { t } = useI18n();
  const { config, isLoading, error } = useMCPConfig();
  const adminRequired =
    error instanceof MCPConfigRequestError && error.isAdminRequired;
  const resetCache = useResetMCPCache();
  return (
    <SettingsSection
      title={t.settings.tools.title}
      description={t.settings.tools.description}
    >
      {isLoading ? (
        <div className="text-muted-foreground text-sm">{t.common.loading}</div>
      ) : adminRequired ? (
        <div className="text-muted-foreground text-sm">
          {t.settings.tools.adminRequired}
        </div>
      ) : error ? (
        <div>Error: {error.message}</div>
      ) : (
        <div className="flex w-full flex-col gap-4">
          <MCPServerList servers={config?.mcp_servers} />
          <ReloadCacheButton
            onReload={() =>
              resetCache.mutate(undefined, {
                onSuccess: () => toast.success(t.settings.tools.cacheReloaded),
                onError: (reseterror) =>
                  toast.error(
                    reseterror instanceof Error
                      ? reseterror.message
                      : "Failed to reload MCP cache.",
                  ),
              })
            }
            busy={resetCache.isPending}
            hint={t.settings.tools.reloadCacheHint}
          />
        </div>
      )}
    </SettingsSection>
  );
}

function ReloadCacheButton({
  onReload,
  busy,
  hint,
}: {
  onReload: () => void;
  busy: boolean;
  hint: string;
}) {
  const { t } = useI18n();
  return (
    <div className="flex w-full items-center justify-between gap-4 rounded-lg border p-3">
      <div className="min-w-0 text-sm">
        <div className="font-medium">{t.settings.tools.reloadCache}</div>
        <p className="text-muted-foreground text-xs">{hint}</p>
      </div>
      <Button
        variant="outline"
        size="sm"
        className="shrink-0"
        onClick={onReload}
        disabled={busy}
      >
        {busy ? (
          <LoaderCircleIcon className="size-4 animate-spin" />
        ) : (
          <LoaderCircleIcon className="size-4" />
        )}
        {t.settings.tools.reloadCache}
      </Button>
    </div>
  );
}

function MCPServerList({
  servers,
}: {
  servers?: Record<string, MCPServerConfig>;
}) {
  const { t } = useI18n();
  const strings = t.settings.tools;
  const { mutate: enableMCPServer } = useEnableMCPServer();
  const deleteServer = useDeleteMCPServer();
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [initialForm, setInitialForm] = useState<FormState>(emptyForm());
  const isStatic = env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true";
  const entries = Object.entries(servers ?? {});

  const openAdd = () => {
    setEditingName(null);
    setInitialForm(emptyForm());
    setDialogOpen(true);
  };

  const openEdit = (name: string, server: MCPServerConfig) => {
    setEditingName(name);
    setInitialForm(formFromServer(name, server));
    setDialogOpen(true);
  };

  const handleDelete = (name: string) => {
    if (confirmingDelete !== name) {
      setConfirmingDelete(name);
      setTimeout(
        () =>
          setConfirmingDelete((current) => (current === name ? null : current)),
        3000,
      );
      return;
    }
    deleteServer
      .mutateAsync(name)
      .then(() => toast.success(strings.deleted))
      .catch((error: Error) => toast.error(error.message));
  };

  return (
    <div className="flex w-full flex-col gap-4">
      {entries.length === 0 ? (
        <div className="text-muted-foreground text-sm">{strings.empty}</div>
      ) : (
        entries.map(([name, config]) => (
          <Item className="w-full" variant="outline" key={name}>
            <ItemContent>
              <ItemTitle>
                <div className="flex items-center gap-2">{name}</div>
              </ItemTitle>
              <ItemDescription className="line-clamp-4">
                {config.description}
              </ItemDescription>
            </ItemContent>
            <ItemActions>
              <Switch
                checked={config.enabled}
                disabled={isStatic}
                onCheckedChange={(checked) =>
                  enableMCPServer({ serverName: name, enabled: checked })
                }
              />
              <Button
                variant="outline"
                size="sm"
                disabled={isStatic}
                onClick={() => openEdit(name, config)}
              >
                <PencilIcon className="size-4" />
              </Button>
              <Button
                variant={confirmingDelete === name ? "destructive" : "outline"}
                size="sm"
                disabled={isStatic || deleteServer.isPending}
                onClick={() => handleDelete(name)}
              >
                <Trash2Icon className="size-4" />
                {confirmingDelete === name ? strings.confirmDelete : null}
              </Button>
            </ItemActions>
          </Item>
        ))
      )}
      <Button
        variant="outline"
        className="self-start"
        disabled={isStatic}
        onClick={openAdd}
      >
        <PlusIcon className="size-4" />
        {strings.addButton}
      </Button>
      {dialogOpen && (
        <MCPServerFormDialog
          key={editingName ?? "new"}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          editingName={editingName}
          initialForm={initialForm}
        />
      )}
    </div>
  );
}
