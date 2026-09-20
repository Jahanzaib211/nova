# Capability registry — declared once, reachable everywhere

Every feature Nova ships declares its operations **once**, as a
`CapabilityModule` in `deerflow.capabilities`. From that single declaration
the platform derives every surface, so a capability cannot exist for the UI
and be missing for the agent, or exist for the agent and be invisible to an
external harness:

| Surface | Derived how | Who uses it |
|---|---|---|
| `GET /api/capabilities/ops` | `registry.snapshot()` + per-module status + feature flags | Settings pages, the ops console |
| `POST /api/capabilities/ops/{name}` | validates the body against the op's input model, runs the handler as the session user | the typed frontend client |
| Lead-agent tools | `deerflow.tools.capability_tools` → one `StructuredTool` per op, gated by `tool_groups: [{name: nova:<module>}]` in `config.yaml` | Nova's own agent (the harness) |
| Nova MCP server `/api/mcp/nova` | `app.gateway.mcp_server` → one MCP tool per `mcp=True` op, authenticated by a **harness token** | Claude Code (ACP), OpenClaw, scripts |
| `features` in `/api/runtime/capabilities` | `deerflow.capabilities.modules.features.compute_flags()` — the only place flags are computed | frontend `useFeatureFlags` |
| `contracts/capabilities.baseline.json` | `scripts/capabilities_snapshot.py`; pinned by `tests/test_capability_registry.py` (removals/retypes fail, additions pass) | CI |
| `frontend/src/core/capabilities/generated.ts` | `scripts/gen_capabilities_client.py` from the contract; `--check` in `lint-check.yml` fails on drift | `invoke("jobs.list", {...})` |

## Declaring an operation

```python
from deerflow.capabilities import CapabilityModule, Operation, OpContext, ModuleStatus

class ListIn(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)

class Items(BaseModel):
    items: list[dict]; total: int

async def _list(ctx: OpContext, inp: ListIn) -> Items: ...
async def _status() -> ModuleStatus: ...

MODULE = CapabilityModule(
    id="jobs", title="Jobs", flag="jobs", config_key="jobs", status=_status,
    operations=[
        Operation(name="jobs.list", kind="read", input=ListIn, output=Items, handler=_list,
                  description="List the caller's jobs."),
    ],
)
```

- `name` is `<module>.<op>`; the tool name is `<module>__<op>` (dots are not
  valid in every provider's tool grammar).
- `kind` is `read | write | execute | secret | admin`. `secret` and `admin`
  ops are **never** harness or MCP tools regardless of flags; `admin_only`
  ops require `system_role == admin` on the API.
- `flag` (defaults to the module's) hides the op everywhere while the
  section is disabled; the *contract* still lists it.
- `harness=False` / `mcp=False` opt an op out of a surface (e.g. token
  minting is API-only).
- `OpContext` carries `user_id`, `is_admin`, `thread_id`, `surface`
  (`api` | `harness` | `mcp`). Ownership checks belong in the handler; the
  registry only enforces `admin_only`.

Harness-side modules live in `deerflow/capabilities/modules/*` and register
through `register_builtin_modules`. Modules that need `app.*` objects (auth
provider, user model) live in `app/gateway/capabilities_modules.py` and are
registered at `create_app()` — the harness never imports `app`
(`tests/test_harness_boundary.py`).

## Modules shipped

`jobs` (queue, schedules, workers), `integrations` (probed registry),
`agents` (registry + async delegation), `acp` (ACP agents + policy),
`models` (list + live probe = "Check model"), `skills`, `mcp` (client-side
servers + tools), `secrets` (presence-only; 0600 files under
`~/.nova/secrets`; admin, API-only), `features` (flags), `updates`
(versions), `sessions` (browser sessions, harness tokens).

## Harness tokens and the MCP server

A harness token (`nhk_…`, 256-bit, shown once, stored as SHA-256) pins the
user every MCP call runs as and the modules it may reach (`scopes`, or
`["*"]`). Mint one with `sessions.token_create` (Settings › Devices);
revoke with `sessions.token_revoke` — effective on the next request.

Point a harness at Nova:

```json
{ "mcpServers": { "nova": {
    "type": "http", "url": "https://<gateway>/api/mcp/nova",
    "headers": { "Authorization": "Bearer nhk_…" } } } }
```

Claude Code: `claude mcp add --transport http nova https://<gateway>/api/mcp/nova --header "Authorization: Bearer nhk_…"`.
The endpoint is bearer-only (no cookie, so no CSRF) and stateless
(`json_response`), which is what the streamable-HTTP clients in Claude Code
and OpenClaw expect. `capabilities.mcp_server.enabled: false` turns it off.

## Refreshing the contract

```bash
cd backend
DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_CONFIG_PATH=../config.example.yaml \
  PYTHONPATH=. uv run python scripts/capabilities_snapshot.py > ../contracts/capabilities.baseline.json
python3 scripts/gen_capabilities_client.py      # regenerates the TS client (prettier-formatted)
```

Both files ship in the same commit as the declaration change.
