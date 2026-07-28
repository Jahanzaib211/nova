# Open Core Model — Nova

> **Purpose:** Explicitly define what is MIT-licensed (community core) vs. proprietary (commercial tier) in the Nova repository. This document is the single source of truth for licensing boundaries.

---

## License Summary

| Layer | License | Copyright Holder |
|---|---|---|
| **Upstream DeerFlow** (base agent harness) | MIT | ByteDance Ltd. / DeerFlow Authors |
| **Nova OSS Contributions** (this repo's additions to DeerFlow) | MIT | Ali Technologies |
| **Nova Proprietary Additions** (Agent's Computer UI, per-thread isolation, watchdogs, receipts, branding) | **Proprietary / Commercial** | Ali Technologies |

The `LICENSE` file at the repository root contains the **unmodified MIT license** from the upstream DeerFlow project. All upstream copyright notices are preserved per MIT §3.

The `NOTICE.md` file carves out the proprietary additions (see below).

---

## What is MIT (Community Core)

Everything that originated from or is a direct contribution to the upstream DeerFlow agent harness:

- LangGraph-based agent orchestration (`deerflow.agents.*`)
- Subagent delegation system (`deerflow.subagents.*`)
- Sandbox execution providers (Local, AIO/Docker) (`deerflow.sandbox.*`)
- Tool registry + 26 builtin tools (`deerflow.tools.*`)
- MCP integration (`deerflow.mcp.*`)
- Skills discovery/loading (`deerflow.skills.*`)
- Model factory with thinking/vision support (`deerflow.models.*`)
- Configuration system (`deerflow.config.*`)
- Memory extraction/storage (`deerflow.agents.memory.*`)
- Tracing (LangSmith/Langfuse) (`deerflow.tracing.*`)
- Reflection/dynamic loading (`deerflow.reflection.*`)
- Community tool providers (Tavily, Jina, Firecrawl, etc.)
- Embedded Python client (`deerflow.client.*`)
- All upstream tests and CI configurations

**Nova-specific MIT contributions** (additions on top of DeerFlow that we release under MIT):

- Hardened browser subsystem (circuit breaker, retry, tracing, metrics, shutdown)
- Execution kernel (PTY manager, session registry, two-phase cancellation, budgets)
- Recovery engine + declarative policies
- Event bus + domain events + lifecycle state machine
- Typed service layer (protocols + implementations)
- Consolidation CI guardrails (middleware sprawl, duplicate recovery)
- Correlation ID across SSE + diagnostics
- Cross-ref isolation test + cross-project reference ban
- Healthcheck watchdog (12 probes) + auto-recovery
- Ollama/LiteLLM free-model gateway integration
- Dify + OpenCode ecosystem wiring
- All Phase C0–C8 platform consolidation work

---

## What is Proprietary (Commercial Tier)

The following Nova-specific additions are **NOT licensed under MIT**. They are proprietary to Ali Technologies and require a commercial license for production use beyond evaluation:

| Component | Description | Location |
|---|---|---|
| **Agent's Computer UI** | 6-tab live panel (Terminal, Editor with red/green live diff, Browser preview, Activity timeline, Files, Review) streaming the agent's own computer in real time | `frontend/src/components/workspace/agent-computer/` |
| **Per-thread container isolation** | Docker-per-thread sandbox with virtual path translation (`/mnt/user-data/{workspace,uploads,outputs}`) and ACP workspace mounting | `backend/packages/harness/deerflow/sandbox/` (AioSandboxProvider + path mappings) |
| **Watchdog/Receipts/Self-improvement loop** | 12-probe self-healing watchdog (P1–P12), PM2-owned Docker lifecycle, reboot persistence, tunnel auto-recovery, structured post-run receipts, autonomous improvement loop | `scripts/healthcheck-daemon.py`, `scripts/pm2-monitoring.sh`, `backend/packages/harness/deerflow/runtime/` |
| **Nova branding** | "Nova — The Agent's Computer", "Made by Ali Technologies", logos, visual identity | `NOTICE.md`, `README.md` hero, UI strings |

**Commercial license terms** are negotiated separately. Contact `alilabsx@gmail.com` for evaluation access or licensing.

---

## Boundary Enforcement

- The proprietary UI components are in a dedicated frontend subtree (`agent-computer/`) and are not required for the core agent to function — the upstream DeerFlow chat UI works without them.
- The per-thread isolation logic lives in the sandbox provider layer; the LocalSandboxProvider (MIT) provides file-tools without container isolation.
- The watchdog runs as a separate PM2 process (`nova-monitoring`, `nova-healthcheck`) and is not imported by the core agent runtime.
- All proprietary code is clearly marked in `NOTICE.md` with copyright attribution to Ali Technologies.

---

## Contributing

- **MIT-layer contributions** (bug fixes, features to the agent harness, sandbox, tools, etc.) are accepted via PR under the standard MIT terms. By contributing, you agree your contributions are licensed under MIT.
- **Proprietary-layer contributions** are not accepted from external contributors — the commercial tier is maintained exclusively by Ali Technologies.

---

## FAQ

**Q: Can I use Nova for free?**
A: Yes. The core agent harness (DeerFlow + Nova MIT additions) is fully functional under MIT. You get the agent, subagents, sandbox, tools, skills, memory, MCP, tracing, and all Phase C0–C8 platform work. The proprietary tier adds the live "Agent's Computer" UI, per-thread Docker isolation, and the enterprise watchdog/receipts loop.

**Q: Can I build my own UI on top of the MIT core?**
A: Absolutely. The MIT core exposes a complete REST + SSE API (`/api/*`, `/api/langgraph/*`). The embedded `NovaClient` provides in-process access. Many teams use just the backend as an agent runtime with custom frontends.

**Q: What if I want the proprietary features?**
A: Contact `alilabsx@gmail.com` for an evaluation license or commercial terms.

**Q: Does the MIT core depend on the proprietary code?**
A: No. The dependency direction is strictly: proprietary → MIT. The MIT core has zero imports or runtime dependencies on proprietary components.

---

## Legal

- Upstream DeerFlow: MIT License, Copyright (c) 2025 ByteDance Ltd. and/or its affiliates
- Nova OSS additions: MIT License, Copyright (c) 2025-2026 Ali Technologies
- Nova Proprietary additions: All rights reserved, Ali Technologies

See `LICENSE` (MIT text) and `NOTICE.md` (attribution + proprietary carve-out) for the legally binding terms.
