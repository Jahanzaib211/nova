# Agent registry and async delegation

## Registry

`GET /api/agents/registry` (session auth) is a read model over config and
the job runner — no table of its own:

| kind | source | runner |
|---|---|---|
| `lead` | the first model in `models:` | gateway |
| `subagent` | `BUILTIN_SUBAGENTS` + `subagents.custom_agents` (+ `subagents.agents` overrides) | jobs when `subagents.async_enabled`, else gateway |
| `custom` | the agents API (`list_custom_agents`) | gateway |
| `acp` | `acp_agents` | jobs when `subagents.async_enabled`, else gateway |

Each entry carries `queued` / `running` counts of the caller's own
`agents.task` jobs for that agent. Rendered on `/workspace/agents` and as the
Agents card on `/workspace/jobs` (`src/features/agents-registry`).

## Async delegation (`subagents.async_enabled: true`, config_version 25)

Two tools join the lead agent's set (never a subagent's — no nesting):

- `delegate_async(agent, task)` → enqueues `agents.task` on the `agents`
  queue (owner + thread id carried, `max_attempts: 1`) and returns the job id.
- `check_delegation(job_id)` → status/progress, or the result once succeeded.

`agents.task` (`app/jobs/handlers/agents.py`) runs in the jobs container:
an ACP agent through the same `invoke_acp_agent` code path (the acp/cli-auth
overlays now mount into `jobs` too), or a subagent through
`SubagentExecutor` with progress per AI message. The result lands in
`result_json.result` and, when the job carries a thread id, in
`/mnt/user-data/outputs/agent-tasks/<job_id>.md` of that thread. The user
follows it on the Jobs page.

Limit, stated plainly: the jobs container has no sandbox provider (no docker
socket), so a subagent that calls bash/file tools fails at that call with a
clear error. Prefer ACP agents or research-style subagents for async work.
This is swarm step 1 — bounded by `jobs.concurrency` on the `agents` queue.
