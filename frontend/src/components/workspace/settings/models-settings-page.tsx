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
import { getModelLabel } from "@/core/models/types";
import type { Model, ModelWriteRequest } from "@/core/models/types";

import { SettingsSection } from "./settings-section";

const OPENAI_COMPATIBLE = "langchain_openai:ChatOpenAI";
const ANTHROPIC = "langchain_anthropic:ChatAnthropic";
const CUSTOM = "__custom__";

// llama.cpp's own default is :8080, and this preset has long used :8081.
// Neither is free on the Nova host: mailcow's nginx holds 127.0.0.1:8080 and
// hsproxy holds 127.0.0.1:8081 (the same collision that made the sandbox base
// port move to 8180). A user who clicks this preset without changing the port
// therefore reaches hsproxy and gets `{"error":"Unrecognized request URL"}`,
// which looks like a Nova bug rather than a wrong address.
//
// Left at the convention rather than invented: llama-server is not running
// here at all, so any port would be a guess. The placeholder below matches, and
// the point of a preset is that it is edited.
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
  amd_compute: "",
  showInChat: true,
  // Left blank on purpose: only whoever started llama-server knows its `-c`.
  max_input_tokens: "",
};

// Ollama models served through the nova-litellm proxy (docker/litellm/config.yaml).
// The gateway runs in Docker, so the base_url targets the host's docker bridge
// via host.docker.internal — not 127.0.0.1.
const OLLAMA_PRESET: FormState = {
  name: "",
  display_name: "",
  model: "qwen2.5-7b-local",
  providerChoice: OPENAI_COMPATIBLE,
  customUse: "",
  base_url: "http://host.docker.internal:4000/v1",
  api_key: "not-needed",
  supports_thinking: false,
  supports_reasoning_effort: false,
  supports_vision: false,
  amd_compute: "",
  showInChat: true,
  // Benchmarked for qwen2.5:7b-instruct on this host (docs/LOCAL_MODELS.md).
  // Change it with the model — it is not a global default.
  max_input_tokens: "32768",
};

// Fireworks AI — managed inference served on AMD Instinct MI300X GPUs
// (OpenAI-compatible). Set FIREWORKS_API_KEY in the environment; api_key is left
// as a $-placeholder so the key is resolved from env, never persisted in plain
// text. Gemma models (accounts/fireworks/models/gemma-3-27b-it) additionally
// qualify for the hackathon Gemma side prize.
const FIREWORKS_PRESET: FormState = {
  name: "",
  display_name: "Fireworks (AMD MI300X)",
  model: "accounts/fireworks/models/llama-v4-maverick",
  providerChoice: OPENAI_COMPATIBLE,
  customUse: "",
  base_url: "https://api.fireworks.ai/inference/v1",
  api_key: "$FIREWORKS_API_KEY",
  supports_thinking: false,
  supports_reasoning_effort: false,
  supports_vision: true,
  amd_compute: "",
  showInChat: true,
  max_input_tokens: "",
};

// AMD Developer Cloud — Nova's own inference on a bare-metal AMD Instinct GPU via
// vLLM on ROCm (native VllmChatModel provider). Replace <amd-droplet-ip> with the
// droplet address. Gemma is the default (strong on MI300X + side-prize eligible).
const AMD_CLOUD_PRESET: FormState = {
  name: "",
  display_name: "AMD Instinct (vLLM/ROCm)",
  model: "google/gemma-3-27b-it",
  providerChoice: CUSTOM,
  customUse: "deerflow.models.vllm_provider:VllmChatModel",
  base_url: "http://<amd-droplet-ip>:8000/v1",
  api_key: "not-needed",
  supports_thinking: false,
  supports_reasoning_effort: false,
  supports_vision: false,
  amd_compute: "AMD Instinct MI300X (vLLM/ROCm)",
  showInChat: true,
  max_input_tokens: "",
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
  amd_compute: string;
  /** Inverted in the UI: the switch reads "Show in chat", the API field is `hidden`. */
  showInChat: boolean;
  /** Kept as a string so the input can be cleared; "" means "no explicit window". */
  max_input_tokens: string;
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
    amd_compute: "",
    showInChat: true,
    max_input_tokens: "",
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
    amd_compute: model.amd_compute ?? "",
    showInChat: !(model.hidden ?? false),
    max_input_tokens: model.max_input_tokens
      ? String(model.max_input_tokens)
      : "",
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
    hidden: !form.showInChat,
  };
  const ctx = Number.parseInt(form.max_input_tokens.trim(), 10);
  if (Number.isFinite(ctx) && ctx > 0) {
    request.max_input_tokens = ctx;
  }
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
  if (form.amd_compute.trim()) {
    request.amd_compute = form.amd_compute.trim();
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
    // Stacked on phones — inside the dialog there is only ~340px to split, so
    // a 1/3 label column would squeeze the input past usability.
    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3 sm:items-center sm:gap-3">
      <div className="text-sm font-medium">{label}</div>
      <div className="sm:col-span-2">{children}</div>
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
              placeholder="local-llm"
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
              placeholder="model-id"
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
          <FieldRow label={strings.fieldShowInChat}>
            <div className="flex flex-col gap-1">
              <Switch
                checked={form.showInChat}
                onCheckedChange={(v) => set("showInChat", v)}
              />
              <span className="text-muted-foreground text-xs">
                {strings.fieldShowInChatHint}
              </span>
            </div>
          </FieldRow>
          <FieldRow label={strings.fieldMaxInputTokens}>
            <div className="flex flex-col gap-1">
              <Input
                inputMode="numeric"
                value={form.max_input_tokens}
                placeholder="32768"
                onChange={(e) =>
                  set("max_input_tokens", e.target.value.replace(/[^0-9]/g, ""))
                }
              />
              <span className="text-muted-foreground text-xs">
                {strings.fieldMaxInputTokensHint}
              </span>
            </div>
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
          {getModelLabel(model)}
          <Badge variant={isRuntime ? "default" : "secondary"}>
            {isRuntime ? (
              strings.sourceRuntime
            ) : (
              <>
                <LockIcon className="size-3" /> {strings.sourceConfig}
              </>
            )}
          </Badge>
          {model.amd_compute ? (
            <Badge variant="default" title={model.amd_compute}>
              <CpuIcon className="size-3" /> AMD
            </Badge>
          ) : null}
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
            <CircleCheckIcon className="text-success size-4" />
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
          <div className="flex flex-wrap gap-2">
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
            <Button
              variant="outline"
              onClick={() => openAdd({ ...OLLAMA_PRESET })}
            >
              <ServerIcon className="size-4" />
              {strings.addOllamaButton}
            </Button>
            <Button
              variant="outline"
              onClick={() => openAdd({ ...FIREWORKS_PRESET })}
            >
              <CpuIcon className="size-4" />
              {strings.addFireworksButton}
            </Button>
            <Button
              variant="outline"
              onClick={() => openAdd({ ...AMD_CLOUD_PRESET })}
            >
              <CpuIcon className="size-4" />
              {strings.addAmdCloudButton}
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
