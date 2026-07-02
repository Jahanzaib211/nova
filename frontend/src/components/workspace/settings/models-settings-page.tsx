"use client";

import {
  CircleCheckIcon,
  CircleXIcon,
  CpuIcon,
  LoaderCircleIcon,
  LockIcon,
  PencilIcon,
  PlusIcon,
  ServerIcon,
  Trash2Icon,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
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
  ItemMedia,
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
import { useI18n } from "@/core/i18n/hooks";
import {
  useCreateModel,
  useDeleteModel,
  useModels,
  useTestModel,
  useUpdateModel,
} from "@/core/models/hooks";
import type { Model, ModelWriteRequest } from "@/core/models/types";

import { SettingsSection } from "./settings-section";

const OPENAI_COMPATIBLE = "langchain_openai:ChatOpenAI";
const ANTHROPIC = "langchain_anthropic:ChatAnthropic";
const CUSTOM = "__custom__";

const LLAMA_CPP_PRESET: FormState = {
  name: "",
  display_name: "",
  model: "",
  providerChoice: OPENAI_COMPATIBLE,
  customUse: "",
  base_url: "http://127.0.0.1:8081/v1",
  api_key: "not-needed",
  supports_thinking: false,
  supports_reasoning_effort: false,
  supports_vision: false,
};

interface FormState {
  name: string;
  display_name: string;
  model: string;
  providerChoice: string;
  customUse: string;
  base_url: string;
  api_key: string;
  supports_thinking: boolean;
  supports_reasoning_effort: boolean;
  supports_vision: boolean;
}

function emptyForm(): FormState {
  return {
    name: "",
    display_name: "",
    model: "",
    providerChoice: OPENAI_COMPATIBLE,
    customUse: "",
    base_url: "",
    api_key: "",
    supports_thinking: false,
    supports_reasoning_effort: false,
    supports_vision: false,
  };
}

function formFromModel(model: Model): FormState {
  const use = model.use ?? OPENAI_COMPATIBLE;
  const isKnown = use === OPENAI_COMPATIBLE || use === ANTHROPIC;
  return {
    name: model.name,
    display_name: model.display_name ?? "",
    model: model.model,
    providerChoice: isKnown ? use : CUSTOM,
    customUse: isKnown ? "" : use,
    base_url: model.base_url ?? "",
    // Never echoed back by the API; empty means "keep the stored key" on update.
    api_key: "",
    supports_thinking: model.supports_thinking ?? false,
    supports_reasoning_effort: model.supports_reasoning_effort ?? false,
    supports_vision: model.supports_vision ?? false,
  };
}

function formToRequest(form: FormState, isEdit: boolean): ModelWriteRequest {
  const use =
    form.providerChoice === CUSTOM
      ? form.customUse.trim()
      : form.providerChoice;
  const request: ModelWriteRequest = {
    name: form.name.trim(),
    model: form.model.trim(),
    use,
    supports_thinking: form.supports_thinking,
    supports_reasoning_effort: form.supports_reasoning_effort,
    supports_vision: form.supports_vision,
  };
  if (form.display_name.trim()) {
    request.display_name = form.display_name.trim();
  }
  if (form.base_url.trim()) {
    request.base_url = form.base_url.trim();
  }
  if (form.api_key.trim()) {
    request.api_key = form.api_key.trim();
  } else if (!isEdit) {
    request.api_key = undefined;
  }
  return request;
}

function FieldRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-3 items-center gap-3">
      <div className="text-sm font-medium">{label}</div>
      <div className="col-span-2">{children}</div>
    </div>
  );
}

function ModelFormDialog({
  open,
  onOpenChange,
  editing,
  initialForm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing: Model | null;
  initialForm: FormState;
}) {
  const { t } = useI18n();
  const [form, setForm] = useState<FormState>(initialForm);
  const createModel = useCreateModel();
  const updateModel = useUpdateModel();
  const isEdit = editing !== null;
  const isPending = createModel.isPending || updateModel.isPending;
  const strings = t.settings.models;

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const canSubmit =
    form.name.trim().length > 0 &&
    form.model.trim().length > 0 &&
    (form.providerChoice !== CUSTOM || form.customUse.trim().length > 0);

  const handleSubmit = () => {
    const request = formToRequest(form, isEdit);
    const mutation = isEdit
      ? updateModel.mutateAsync({ name: editing.name, request })
      : createModel.mutateAsync(request);
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
        <div className="space-y-3">
          <FieldRow label={strings.fieldName}>
            <Input
              value={form.name}
              disabled={isEdit}
              placeholder="ornith-local"
              onChange={(e) => set("name", e.target.value)}
            />
          </FieldRow>
          <FieldRow label={strings.fieldDisplayName}>
            <Input
              value={form.display_name}
              placeholder="Ornith (local)"
              onChange={(e) => set("display_name", e.target.value)}
            />
          </FieldRow>
          <FieldRow label={strings.fieldProvider}>
            <Select
              value={form.providerChoice}
              onValueChange={(v) => set("providerChoice", v)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={OPENAI_COMPATIBLE}>
                  {strings.providerOpenAICompatible}
                </SelectItem>
                <SelectItem value={ANTHROPIC}>
                  {strings.providerAnthropic}
                </SelectItem>
                <SelectItem value={CUSTOM}>{strings.providerCustom}</SelectItem>
              </SelectContent>
            </Select>
          </FieldRow>
          {form.providerChoice === CUSTOM && (
            <FieldRow label={strings.fieldCustomClass}>
              <Input
                value={form.customUse}
                placeholder="langchain_openai:ChatOpenAI"
                onChange={(e) => set("customUse", e.target.value)}
              />
            </FieldRow>
          )}
          <FieldRow label={strings.fieldModelId}>
            <Input
              value={form.model}
              placeholder="ornith"
              onChange={(e) => set("model", e.target.value)}
            />
          </FieldRow>
          <FieldRow label={strings.fieldBaseUrl}>
            <Input
              value={form.base_url}
              placeholder="http://127.0.0.1:8081/v1"
              onChange={(e) => set("base_url", e.target.value)}
            />
          </FieldRow>
          <FieldRow label={strings.fieldApiKey}>
            <Input
              type="password"
              value={form.api_key}
              placeholder={isEdit ? strings.apiKeyUnchanged : "sk-..."}
              autoComplete="off"
              onChange={(e) => set("api_key", e.target.value)}
            />
          </FieldRow>
          <FieldRow label={strings.fieldThinking}>
            <Switch
              checked={form.supports_thinking}
              onCheckedChange={(v) => set("supports_thinking", v)}
            />
          </FieldRow>
          <FieldRow label={strings.fieldReasoningEffort}>
            <Switch
              checked={form.supports_reasoning_effort}
              onCheckedChange={(v) => set("supports_reasoning_effort", v)}
            />
          </FieldRow>
          <FieldRow label={strings.fieldVision}>
            <Switch
              checked={form.supports_vision}
              onCheckedChange={(v) => set("supports_vision", v)}
            />
          </FieldRow>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t.common.cancel}
          </Button>
          <Button disabled={!canSubmit || isPending} onClick={handleSubmit}>
            {isPending && <LoaderCircleIcon className="size-4 animate-spin" />}
            {isEdit ? strings.saveButton : strings.addButton}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ModelItem({
  model,
  onEdit,
}: {
  model: Model;
  onEdit: (model: Model) => void;
}) {
  const { t } = useI18n();
  const strings = t.settings.models;
  const testModel = useTestModel();
  const deleteModel = useDeleteModel();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const isRuntime = model.source === "runtime";
  const isLocal = Boolean(
    model.base_url &&
    (model.base_url.includes("127.0.0.1") ||
      model.base_url.includes("localhost")),
  );

  const handleTest = () => {
    testModel
      .mutateAsync(model.name)
      .then((result) => {
        if (result.ok) {
          toast.success(`${model.name}: ${result.message}`);
        } else {
          toast.error(`${model.name}: ${result.message}`);
        }
      })
      .catch((error: Error) => toast.error(error.message));
  };

  const handleDelete = () => {
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      setTimeout(() => setConfirmingDelete(false), 3000);
      return;
    }
    deleteModel
      .mutateAsync(model.name)
      .then(() => toast.success(strings.deleted))
      .catch((error: Error) => toast.error(error.message));
  };

  return (
    <Item variant="outline">
      <ItemMedia>
        {isLocal ? (
          <ServerIcon className="size-5" />
        ) : (
          <CpuIcon className="size-5" />
        )}
      </ItemMedia>
      <ItemContent>
        <ItemTitle>
          {model.display_name?.trim() ? model.display_name : model.name}
          <Badge variant={isRuntime ? "default" : "secondary"}>
            {isRuntime ? (
              strings.sourceRuntime
            ) : (
              <>
                <LockIcon className="size-3" /> {strings.sourceConfig}
              </>
            )}
          </Badge>
        </ItemTitle>
        <ItemDescription>
          {model.model}
          {model.base_url ? ` · ${model.base_url}` : ""}
        </ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button
          variant="outline"
          size="sm"
          disabled={testModel.isPending}
          onClick={handleTest}
        >
          {testModel.isPending ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : testModel.data?.ok === true ? (
            <CircleCheckIcon className="size-4 text-green-600" />
          ) : testModel.data?.ok === false ? (
            <CircleXIcon className="text-destructive size-4" />
          ) : null}
          {strings.testButton}
        </Button>
        {isRuntime && (
          <>
            <Button variant="outline" size="sm" onClick={() => onEdit(model)}>
              <PencilIcon className="size-4" />
            </Button>
            <Button
              variant={confirmingDelete ? "destructive" : "outline"}
              size="sm"
              disabled={deleteModel.isPending}
              onClick={handleDelete}
            >
              <Trash2Icon className="size-4" />
              {confirmingDelete ? strings.confirmDelete : null}
            </Button>
          </>
        )}
      </ItemActions>
    </Item>
  );
}

export function ModelsSettingsPage() {
  const { t } = useI18n();
  const strings = t.settings.models;
  const { models, isLoading, error } = useModels();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Model | null>(null);
  const [initialForm, setInitialForm] = useState<FormState>(emptyForm());

  const openAdd = (preset?: FormState) => {
    setEditing(null);
    setInitialForm(preset ?? emptyForm());
    setDialogOpen(true);
  };

  const openEdit = (model: Model) => {
    setEditing(model);
    setInitialForm(formFromModel(model));
    setDialogOpen(true);
  };

  return (
    <SettingsSection title={strings.title} description={strings.description}>
      {isLoading ? (
        <div className="text-muted-foreground text-sm">{t.common.loading}</div>
      ) : error ? (
        <div className="text-destructive text-sm">{strings.loadError}</div>
      ) : (
        <div className="flex w-full flex-col gap-4">
          {models.map((model) => (
            <ModelItem key={model.name} model={model} onEdit={openEdit} />
          ))}
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => openAdd()}>
              <PlusIcon className="size-4" />
              {strings.addButton}
            </Button>
            <Button
              variant="outline"
              onClick={() => openAdd({ ...LLAMA_CPP_PRESET })}
            >
              <ServerIcon className="size-4" />
              {strings.addLlamaCppButton}
            </Button>
          </div>
        </div>
      )}
      {dialogOpen && (
        <ModelFormDialog
          key={editing?.name ?? "new"}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          editing={editing}
          initialForm={initialForm}
        />
      )}
    </SettingsSection>
  );
}
