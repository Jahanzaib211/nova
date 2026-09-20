// GENERATED FILE — do not edit.
// Source: contracts/capabilities.baseline.json via backend/scripts/gen_capabilities_client.py
// Regenerate: cd backend && PYTHONPATH=. uv run python scripts/gen_capabilities_client.py

export interface AcpAgentsInput {
  // no fields
}

export interface AcpAgentsOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface AgentsDelegateInput {
  /** Agent name from agents.registry */
  agent: string;
  /** What the agent should do; the result lands in outputs/agent-tasks/<job_id>.md */
  task: string;
}

export interface AgentsDelegateOutput {
  job: Record<string, unknown>;
}

export interface AgentsRegistryInput {
  // no fields
}

export interface AgentsRegistryOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface FeaturesGetInput {
  // no fields
}

export interface FeaturesGetOutput {
  flags: Record<string, boolean>;
}

export interface IntegrationsListInput {
  /** Bypass the probe cache */
  refresh?: boolean;
}

export interface IntegrationsListOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface IntegrationsProbeInput {
  integration_id: string;
}

export interface IntegrationsProbeOutput {
  item: Record<string, unknown>;
}

export interface JobsCancelInput {
  job_id: string;
}

export interface JobsCancelOutput {
  detail?: null | string;
  ok?: boolean;
}

export interface JobsEnqueueInput {
  payload?: Record<string, unknown>;
  queue?: string;
  /** Delay before the job becomes claimable */
  run_after_seconds?: null | number;
  /** Registered job type */
  type: string;
}

export interface JobsEnqueueOutput {
  job: Record<string, unknown>;
}

export interface JobsEventsInput {
  job_id: string;
}

export interface JobsEventsOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface JobsGetInput {
  job_id: string;
}

export interface JobsGetOutput {
  job: Record<string, unknown>;
}

export interface JobsListInput {
  limit?: number;
  /** queued | running | succeeded | failed | dead_letter | cancelled */
  status?: null | string;
  /** Job type, e.g. em.campaign.start or agents.task */
  type?: null | string;
}

export interface JobsListOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface JobsRetryInput {
  job_id: string;
}

export interface JobsRetryOutput {
  detail?: null | string;
  ok?: boolean;
}

export interface JobsScheduleDeleteInput {
  schedule_id: string;
}

export interface JobsScheduleDeleteOutput {
  detail?: null | string;
  ok?: boolean;
}

export interface JobsScheduleUpsertInput {
  /** 5-field cron expression */
  cron: string;
  enabled?: boolean;
  name: string;
  payload?: Record<string, unknown>;
  queue?: string;
  timezone?: string;
  type: string;
}

export interface JobsScheduleUpsertOutput {
  job: Record<string, unknown>;
}

export interface JobsSchedulesInput {
  // no fields
}

export interface JobsSchedulesOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface JobsSummaryInput {
  // no fields
}

export interface JobsSummaryOutput {
  job: Record<string, unknown>;
}

export interface JobsWorkersInput {
  // no fields
}

export interface JobsWorkersOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface McpServersInput {
  // no fields
}

export interface McpServersOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface McpToggleInput {
  enabled: boolean;
  /** MCP server name */
  name: string;
}

export interface McpToggleOutput {
  detail?: null | string;
  ok?: boolean;
}

export interface McpToolsInput {
  // no fields
}

export interface McpToolsOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface ModelsListInput {
  // no fields
}

export interface ModelsListOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface ModelsProbeInput {
  /** Model name from models.list */
  name: string;
}

export interface ModelsProbeOutput {
  detail?: null | string;
  latency_ms?: null | number;
  name: string;
  ok: boolean;
}

export interface SecretsListInput {
  // no fields
}

export interface SecretsListOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface SecretsSetInput {
  /** File name under the secrets dir (letters, digits, _ . -) */
  name: string;
  /** The secret. Stored 0600; never echoed back. */
  value: string;
}

export interface SecretsSetOutput {
  detail?: null | string;
  ok?: boolean;
}

export interface SecretsUnsetInput {
  name: string;
}

export interface SecretsUnsetOutput {
  detail?: null | string;
  ok?: boolean;
}

export interface SessionsGetInput {
  // no fields
}

export interface SessionsGetOutput {
  detail: string;
  last_sign_in_at?: null | string;
  token_version: number;
}

export interface SessionsRevokeAllInput {
  // no fields
}

export interface SessionsRevokeAllOutput {
  detail?: null | string;
  ok?: boolean;
}

export interface SessionsTokenCreateInput {
  /** What will use this token, e.g. 'claude-code on laptop' */
  name: string;
  /** Capability module ids this token may invoke, or ['*'] */
  scopes?: Array<string>;
}

export interface SessionsTokenCreateOutput {
  id: string;
  /** Where to point the harness: Nova's MCP endpoint */
  mcp_url: string;
  name: string;
  prefix: string;
  scopes: Array<string>;
  /** Shown once. Put it in the harness's Authorization: Bearer header. */
  token: string;
}

export interface SessionsTokenRevokeInput {
  token_id: string;
}

export interface SessionsTokenRevokeOutput {
  detail?: null | string;
  ok?: boolean;
}

export interface SessionsTokensInput {
  // no fields
}

export interface SessionsTokensOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface SkillsListInput {
  // no fields
}

export interface SkillsListOutput {
  items: Array<Record<string, unknown>>;
  total: number;
}

export interface SkillsToggleInput {
  enabled: boolean;
  /** Skill name */
  name: string;
}

export interface SkillsToggleOutput {
  detail?: null | string;
  ok?: boolean;
}

export interface UpdatesVersionsInput {
  // no fields
}

export interface UpdatesVersionsOutput {
  components: Record<string, unknown>;
}

export interface CapabilityOps {
  "acp.agents": { input: AcpAgentsInput; output: AcpAgentsOutput };
  "agents.delegate": {
    input: AgentsDelegateInput;
    output: AgentsDelegateOutput;
  };
  "agents.registry": {
    input: AgentsRegistryInput;
    output: AgentsRegistryOutput;
  };
  "features.get": { input: FeaturesGetInput; output: FeaturesGetOutput };
  "integrations.list": {
    input: IntegrationsListInput;
    output: IntegrationsListOutput;
  };
  "integrations.probe": {
    input: IntegrationsProbeInput;
    output: IntegrationsProbeOutput;
  };
  "jobs.cancel": { input: JobsCancelInput; output: JobsCancelOutput };
  "jobs.enqueue": { input: JobsEnqueueInput; output: JobsEnqueueOutput };
  "jobs.events": { input: JobsEventsInput; output: JobsEventsOutput };
  "jobs.get": { input: JobsGetInput; output: JobsGetOutput };
  "jobs.list": { input: JobsListInput; output: JobsListOutput };
  "jobs.retry": { input: JobsRetryInput; output: JobsRetryOutput };
  "jobs.schedule_delete": {
    input: JobsScheduleDeleteInput;
    output: JobsScheduleDeleteOutput;
  };
  "jobs.schedule_upsert": {
    input: JobsScheduleUpsertInput;
    output: JobsScheduleUpsertOutput;
  };
  "jobs.schedules": { input: JobsSchedulesInput; output: JobsSchedulesOutput };
  "jobs.summary": { input: JobsSummaryInput; output: JobsSummaryOutput };
  "jobs.workers": { input: JobsWorkersInput; output: JobsWorkersOutput };
  "mcp.servers": { input: McpServersInput; output: McpServersOutput };
  "mcp.toggle": { input: McpToggleInput; output: McpToggleOutput };
  "mcp.tools": { input: McpToolsInput; output: McpToolsOutput };
  "models.list": { input: ModelsListInput; output: ModelsListOutput };
  "models.probe": { input: ModelsProbeInput; output: ModelsProbeOutput };
  "secrets.list": { input: SecretsListInput; output: SecretsListOutput };
  "secrets.set": { input: SecretsSetInput; output: SecretsSetOutput };
  "secrets.unset": { input: SecretsUnsetInput; output: SecretsUnsetOutput };
  "sessions.get": { input: SessionsGetInput; output: SessionsGetOutput };
  "sessions.revoke_all": {
    input: SessionsRevokeAllInput;
    output: SessionsRevokeAllOutput;
  };
  "sessions.token_create": {
    input: SessionsTokenCreateInput;
    output: SessionsTokenCreateOutput;
  };
  "sessions.token_revoke": {
    input: SessionsTokenRevokeInput;
    output: SessionsTokenRevokeOutput;
  };
  "sessions.tokens": {
    input: SessionsTokensInput;
    output: SessionsTokensOutput;
  };
  "skills.list": { input: SkillsListInput; output: SkillsListOutput };
  "skills.toggle": { input: SkillsToggleInput; output: SkillsToggleOutput };
  "updates.versions": {
    input: UpdatesVersionsInput;
    output: UpdatesVersionsOutput;
  };
}

export type CapabilityOpName = keyof CapabilityOps;

export type OpKind = "read" | "write" | "execute" | "secret" | "admin";

export interface OpMeta {
  module: string;
  kind: OpKind;
  description: string;
  flag: string | null;
  admin_only: boolean;
  harness: boolean;
  mcp: boolean;
}

export const OP_META: Record<CapabilityOpName, OpMeta> = {
  "acp.agents": {
    module: "acp",
    kind: "read",
    description:
      "Configured ACP agents, whether their adapter binary is present, and their permission policy.",
    flag: "acp_agents",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "agents.delegate": {
    module: "agents",
    kind: "execute",
    description:
      "Run a task on an agent as a background job (survives reloads).",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "agents.registry": {
    module: "agents",
    kind: "read",
    description:
      "All agents with their kind and the caller's live queued/running task counts.",
    flag: null,
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "features.get": {
    module: "features",
    kind: "read",
    description: "Every feature flag and whether it is on.",
    flag: null,
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "integrations.list": {
    module: "integrations",
    kind: "read",
    description:
      "Every integration with status, endpoint, capabilities and latency.",
    flag: "integrations",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "integrations.probe": {
    module: "integrations",
    kind: "execute",
    description: "Re-probe one integration now.",
    flag: "integrations",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "jobs.cancel": {
    module: "jobs",
    kind: "write",
    description: "Request cooperative cancellation of a job.",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "jobs.enqueue": {
    module: "jobs",
    kind: "execute",
    description: "Enqueue a job of a registered type.",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "jobs.events": {
    module: "jobs",
    kind: "read",
    description: "A job's event log (progress, logs, retries).",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "jobs.get": {
    module: "jobs",
    kind: "read",
    description: "One job with its current status, attempts and result.",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "jobs.list": {
    module: "jobs",
    kind: "read",
    description: "List the caller's jobs (all jobs for admins), newest first.",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "jobs.retry": {
    module: "jobs",
    kind: "write",
    description: "Requeue a failed or dead-letter job.",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "jobs.schedule_delete": {
    module: "jobs",
    kind: "write",
    description: "Delete a cron schedule.",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "jobs.schedule_upsert": {
    module: "jobs",
    kind: "write",
    description: "Create or update a cron schedule by name.",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "jobs.schedules": {
    module: "jobs",
    kind: "read",
    description: "List cron schedules.",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "jobs.summary": {
    module: "jobs",
    kind: "read",
    description: "Queue depth by status.",
    flag: "jobs",
    admin_only: true,
    harness: true,
    mcp: true,
  },
  "jobs.workers": {
    module: "jobs",
    kind: "read",
    description: "Worker processes and their heartbeats (Cloud workers).",
    flag: "jobs",
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "mcp.servers": {
    module: "mcp",
    kind: "read",
    description:
      "Configured MCP servers, transport, enabled state and loaded tool count.",
    flag: null,
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "mcp.toggle": {
    module: "mcp",
    kind: "write",
    description: "Enable or disable an MCP server.",
    flag: null,
    admin_only: true,
    harness: true,
    mcp: true,
  },
  "mcp.tools": {
    module: "mcp",
    kind: "read",
    description: "Tools currently loaded from MCP servers.",
    flag: null,
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "models.list": {
    module: "models",
    kind: "read",
    description:
      "Configured models with provider, capabilities and context window; the first is the default.",
    flag: null,
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "models.probe": {
    module: "models",
    kind: "execute",
    description:
      "Send one tiny completion to a model and report latency or the exact error.",
    flag: null,
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "secrets.list": {
    module: "secrets",
    kind: "read",
    description: "Which secrets exist (presence only), with file mode and age.",
    flag: null,
    admin_only: true,
    harness: false,
    mcp: false,
  },
  "secrets.set": {
    module: "secrets",
    kind: "secret",
    description: "Write a secret file (0600, atomic).",
    flag: null,
    admin_only: true,
    harness: false,
    mcp: false,
  },
  "secrets.unset": {
    module: "secrets",
    kind: "secret",
    description: "Remove a secret file.",
    flag: null,
    admin_only: true,
    harness: false,
    mcp: false,
  },
  "sessions.get": {
    module: "sessions",
    kind: "read",
    description: "The caller's session state.",
    flag: null,
    admin_only: false,
    harness: false,
    mcp: false,
  },
  "sessions.revoke_all": {
    module: "sessions",
    kind: "write",
    description: "Sign out every device.",
    flag: null,
    admin_only: false,
    harness: false,
    mcp: false,
  },
  "sessions.token_create": {
    module: "sessions",
    kind: "secret",
    description: "Mint a harness token; the plaintext is returned once.",
    flag: null,
    admin_only: false,
    harness: false,
    mcp: false,
  },
  "sessions.token_revoke": {
    module: "sessions",
    kind: "write",
    description: "Revoke a harness token immediately.",
    flag: null,
    admin_only: false,
    harness: false,
    mcp: false,
  },
  "sessions.tokens": {
    module: "sessions",
    kind: "read",
    description: "The caller's harness tokens (never the secret).",
    flag: null,
    admin_only: false,
    harness: false,
    mcp: false,
  },
  "skills.list": {
    module: "skills",
    kind: "read",
    description:
      "Every installed skill, its category and whether it is enabled.",
    flag: null,
    admin_only: false,
    harness: true,
    mcp: true,
  },
  "skills.toggle": {
    module: "skills",
    kind: "write",
    description: "Enable or disable a skill.",
    flag: null,
    admin_only: true,
    harness: true,
    mcp: true,
  },
  "updates.versions": {
    module: "updates",
    kind: "read",
    description:
      "Version of each component (config, git, Claude CLI, OpenClaw, Node, ACP adapter).",
    flag: null,
    admin_only: false,
    harness: true,
    mcp: true,
  },
};

export const CAPABILITY_MODULES = [
  {
    id: "acp",
    title: "ACP agents",
    description:
      "External agents reachable over the Agent Client Protocol and the permission policy applied to each.",
    flag: "acp_agents",
    config_key: "acp_agents",
    operations: ["acp.agents"],
  },
  {
    id: "agents",
    title: "Agents",
    description:
      "Lead agent, built-in and custom subagents, ACP agents; async delegation as jobs.",
    flag: null,
    config_key: "subagents",
    operations: ["agents.delegate", "agents.registry"],
  },
  {
    id: "features",
    title: "Labs",
    description:
      "Server-declared feature switches; each maps to a config.yaml section.",
    flag: null,
    config_key: null,
    operations: ["features.get"],
  },
  {
    id: "integrations",
    title: "Integrations",
    description:
      "External services (model gateways, mail/CRM/helpdesk, search/crawl/browser, agent gateways) and their live health.",
    flag: "integrations",
    config_key: "integrations",
    operations: ["integrations.list", "integrations.probe"],
  },
  {
    id: "jobs",
    title: "Jobs",
    description: "Background job runner: queue, schedules (cron), workers.",
    flag: "jobs",
    config_key: "jobs",
    operations: [
      "jobs.cancel",
      "jobs.enqueue",
      "jobs.events",
      "jobs.get",
      "jobs.list",
      "jobs.retry",
      "jobs.schedule_delete",
      "jobs.schedule_upsert",
      "jobs.schedules",
      "jobs.summary",
      "jobs.workers",
    ],
  },
  {
    id: "mcp",
    title: "MCP",
    description:
      "Model Context Protocol servers Nova connects to and the tools they contribute.",
    flag: null,
    config_key: "extensions_config.json",
    operations: ["mcp.servers", "mcp.toggle", "mcp.tools"],
  },
  {
    id: "models",
    title: "Models",
    description:
      "Chat models the gateway can route to, with a live per-model probe.",
    flag: null,
    config_key: "models",
    operations: ["models.list", "models.probe"],
  },
  {
    id: "secrets",
    title: "Secrets",
    description:
      "Named secrets (0600 files under ~/.nova/secrets) and the env-var names config.yaml references. Values are never read back.",
    flag: null,
    config_key: null,
    operations: ["secrets.list", "secrets.set", "secrets.unset"],
  },
  {
    id: "sessions",
    title: "Devices & sessions",
    description:
      "Browser sessions and harness tokens (how Claude Code / OpenClaw authenticate to Nova's MCP server).",
    flag: null,
    config_key: null,
    operations: [
      "sessions.get",
      "sessions.revoke_all",
      "sessions.token_create",
      "sessions.token_revoke",
      "sessions.tokens",
    ],
  },
  {
    id: "skills",
    title: "Skills",
    description: "SKILL.md packages available to the lead agent.",
    flag: null,
    config_key: "skills",
    operations: ["skills.list", "skills.toggle"],
  },
  {
    id: "updates",
    title: "Updates",
    description:
      "Versions of the gateway, adapters and CLIs this deployment runs.",
    flag: null,
    config_key: null,
    operations: ["updates.versions"],
  },
] as const;

export const CONTRACT_VERSION = 1;
