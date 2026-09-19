# Nova — The Agent's Computer

English | [中文](./README_zh.md) | [日本語](./README_ja.md) | [Français](./README_fr.md) | [Русский](./README_ru.md)

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./Makefile)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![CI](https://github.com/Jahanzaib211/nova/actions/workflows/backend-unit-tests.yml/badge.svg)](https://github.com/Jahanzaib211/nova/actions)
[![CodeQL](https://github.com/Jahanzaib211/nova/actions/workflows/codeql.yml/badge.svg)](https://github.com/Jahanzaib211/nova/actions/workflows/codeql.yml)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-blue?logo=dependabot)](https://github.com/dependabot)
[![Security](https://img.shields.io/badge/Security-60%2B%20tools-blueviolet?logo=shield)](#security-arsenal)

**Nova** is a full-stack **computer agent** that researches, codes, creates, and defends. It orchestrates **sub-agents**, **memory**, and **per-thread sandboxes** — powered by **extensible skills**, a live streaming view of the agent's own computer, and a built-in **60+ tool security arsenal** (blue team + red team).

Built by **[Ali Technologies](https://www.alilabsx.com)** on top of [DeerFlow](https://github.com/bytedance/deer-flow) (MIT). Upstream license and all copyright notices preserved — see [License](#license) and [NOTICE.md](./NOTICE.md).

![Nova workspace — the agent builds a tip calculator and previews it live in the Agent's Computer Browser tab](./docs/images/nova-workspace.png)

## Table of Contents

- [Quick Start](#quick-start)
- [What Nova adds on DeerFlow](#what-nova-adds-on-deerflow)
- [Security Arsenal](#security-arsenal) -- blue team, red team, scanning
- [Core Features](#core-features)
- [Sandbox](#sandbox)
- [IM Channels](#im-channels)
- [Tracing](#tracing)
- [Recommended Models](#recommended-models)
- [Embedded Python Client](#embedded-python-client)
- [CI/CD & Testing](#cicd--testing)
- [Contributing](#contributing)
- [License](#license)

## Quick Start

```bash
git clone https://github.com/Jahanzaib211/nova.git && cd nova
make setup          # interactive wizard (~2 min)
make doctor         # verify setup
make docker-start   # or: make dev
```

Access: <http://localhost:2026>

### Configuration

`make setup` generates `config.yaml` + `.env`. Manual config: `make config` copies the full template.

<details>
<summary>Manual model configuration</summary>

```yaml
models:
  - name: gpt-4o
    display_name: GPT-4o
    use: langchain_openai:ChatOpenAI
    model: gpt-4o
    api_key: $OPENAI_API_KEY

  - name: qwen3-32b-vllm
    display_name: Qwen3 32B (vLLM)
    use: deerflow.models.vllm_provider:VllmChatModel
    model: Qwen/Qwen3-32B
    api_key: $VLLM_API_KEY
    base_url: http://localhost:8000/v1
    supports_thinking: true
```

CLI-backed providers (Codex CLI, Claude Code OAuth), OpenRouter, Responses API — see `config.example.yaml`.

</details>

### Deployment Sizing

| Target | Starting | Recommended |
|---|---|---|
| Local dev (`make dev`) | 4 vCPU, 8 GB RAM, 20 GB SSD | 8 vCPU, 16 GB RAM |
| Docker dev (`make docker-start`) | 4 vCPU, 8 GB RAM, 25 GB SSD | 8 vCPU, 16 GB RAM |
| Production (`make up`) | 8 vCPU, 16 GB RAM, 40 GB SSD | 16 vCPU, 32 GB RAM |

### Docker

```bash
make docker-init    # pull sandbox image (once)
make docker-start   # dev (hot-reload)
make up             # production
make down           # stop
```

## What Nova adds on DeerFlow

120,533 lines of new code across 698 new files. Total delta: **+145,947 / −11,844 across 1,148 files**. Audited 2026-09-08; every number reproducible — see [NOVA_VS_DEERFLOW.md](./NOVA_VS_DEERFLOW.md).

- **Agent's Computer** — 7-tab live panel: Files, Terminal, Editor (red/green diff), Browser preview, Telemetry, Review, Privacy. Real-time streaming.
- **Verify loop** — headless-Chromium self-checks against the running dev server. Console errors, blank-render detection, screenshots. Vision path so the model *sees* its build.
- **Deterministic code review** — no-LLM review engine. Plain-English verdict for non-coders, per-file stats and risk flags for devs.
- **Self-correction middlewares** — iteration budgets, dead-end loop detection, preflight quota checks, error decontamination, live task progress.
- **27 built-in agent tools** (up from 2 at fork point, upstream still ships 3) — shell sessions, browser control, scaffold, dev-server lifecycle, code review, skill saving, and more.
- **Runtime model management** — add/switch models via API and settings UI.
- **4-layer sandbox image** — base → tools → dind → android (10.2 GB → 20.4 GB). Go, Rust, Playwright, pandoc, tesseract, nested Docker daemon, full Android SDK. Pinned by digest.
- **Global skill promotion** — agent-authored skills security-scanned and promoted to global registry.
- **Ops layer** — 14-probe self-healing watchdog, PM2 lifecycle, reboot persistence, tunnel auto-recovery.
- **Free models via LiteLLM** — MiniMax M3, Nemotron 3 Super, Qwen3 Coder 480B, GPT-OSS 120B via Ollama.
- **38,782 lines of new tests** — 152 backend test files + 53 frontend test files.

## Security Arsenal

Nova ships a full-stack security capability. **80+ tools and defenses** across application-level blue team, offensive red team, and automated scanning.

### Probe Vibe-Coded Apps

Nova can **build an app and then attack it** — the full build-then-break loop in one agent:

1. **Build** — scaffold a web app (Next.js, Flask, FastAPI, whatever) in the sandbox
2. **Self-audit** — the verify loop catches console errors, blank renders, broken layouts
3. **Red team** — the security skill spins up nuclei, nikto, sqlmap, dalfox, ffuf against the running dev server
4. **Report** — deterministic code review + vulnerability scan output in one place
5. **Fix** — agent patches the issues and re-verifies

This works on Nova's own builds **or any app you point it at**. Paste a URL or drop a repo into the sandbox and say "audit this" — the security skill handles the rest. Dependency-ordered attack chains: recon → web app scan → secret detection → exploitation attempts → forensics → hardening recommendations.

```
# Example: Nova builds a tip calculator, then probes it
> build a tip calculator with auth and a database

# Nova's agent does the build, then:
# - runs nuclei against localhost:3000
# - checks for SQL injection with sqlmap
# - fuzzes endpoints with ffuf
# - scans for XSS with dalfox
# - audits dependencies with trivy
# - outputs a security report
```

### Blue Team (Defensive)

| Layer | What it does |
|---|---|
| **AuthMiddleware** | Fail-closed JWT auth gate on every request. Session versioning (revoke-all via token bump). |
| **CSRFMiddleware** | Double-submit cookie, timing-safe comparison, origin validation. |
| **AuthRateLimitMiddleware** | Sliding-window brute-force protection (10 attempts/60s auth, 60/60s cost). CIDR trust chain. |
| **GuardrailMiddleware** | Pre-tool-call authorization. Pluggable providers: AllowlistProvider, OAP policy, custom. Fail-closed default. |
| **SandboxAuditMiddleware** | Command classifier: **block** (`rm -rf /`, fork bombs, reverse shells, LD_PRELOAD), **warn** (chmod 777, sudo, pip install), **pass**. 100% high-risk recall, 0% false positive rate. |
| **Path traversal protection** | Multi-layer: `../`, backslash, bare root, `cd /`, env var escapes, `file://` URLs, brace expansion. |
| **CSP headers** | `default-src 'self'`, `sandbox allow-scripts allow-same-origin`, `X-Frame-Options: DENY`. Active content forced as download. |
| **Audit trail** | Append-only `admin_audit` table. Auth events, sandbox operations, admin actions logged. |
| **Secrets management** | `secrets-doctor.sh`, `secrets-export.sh` (encrypted backup), K8s bootstrap. Env var resolution, Fernet-encrypted BYOK. |
| **Dependency security** | pip-audit, npm audit, Trivy container scanning, CodeQL SAST (Python + JS/TS), Dependabot. |
| **Visitor security** | Scanner/exploit probe detection, brute-force monitoring, high-4xx IP tracking. Grafana alerting. |
| **Platform guardrails** | CI enforcement: middleware sprawl detection, duplicate recovery, cross-reference isolation, blocking IO gate. |
| **IDS/IPS** | Suricata, Snort, Wazuh/OSSEC (host-based), Falco (runtime container security). |
| **Hardening** | Lynis (system audit), chkrootkit (rootkit detection), OpenVAS (vulnerability scanning). |

### Red Team (Offensive)

All tools are sandboxed at `/mnt/security-toolkit`. The **security skill** (`skills/public/security/SKILL.md`) maps every tool by domain with dependency-ordered attack chains: recon → network map → web app → secrets → exploit → creds → forensics → blue team hardening.

| Category | Tools |
|---|---|
| **Recon** | nmap (7.98), masscan, httpx, subfinder, amass, katana, naabu, gau, waybackurls |
| **Web App** | nikto, sqlmap, dalfox (XSS), ffuf, gobuster, wafw00f, whatweb, OWASP ZAP |
| **Secrets** | gitleaks, trufflehog, semgrep, detect-secrets |
| **Exploitation** | nuclei (template-based), Metasploit (host-only), Empire + Starkiller, Sliver (C2) |
| **Creds/AD** | hydra, hashcat, john, bloodhound-python, NetExec (nxc), responder, Mimikatz |
| **Wireless** | aircrack-ng, wifite, bettercap, Wireshark |
| **Forensics/RE** | Ghidra, Cutter, radare2, Volatility3, Autopsy, Velociraptor, binwalk, foremost, exiftool, yara |
| **K8s Security** | peirates, kube-hunter |
| **OSINT** | sherlock, theHarvester, recon-ng, SpiderFoot |

### Security Scanning

| Tool | Type |
|---|---|
| **CodeQL** | Semantic SAST (Python + JS/TS), weekly + every PR |
| **Trivy** | Container/IaC vulnerability scanning |
| **Semgrep** | Pattern-based SAST |
| **Gitleaks** | Git secret detection |
| **TruffleHog** | Filesystem + git secret scanning |
| **Grype** | Container image vulnerability scanning |
| **pip-audit / npm audit** | Dependency vulnerability scanning |

### Running Security Tests

```bash
cd backend && PYTHONPATH=. uv run pytest tests/test_sandbox_tools_security.py -v
cd backend && PYTHONPATH=. uv run pytest tests/test_guardrail*.py -v
cd backend && PYTHONPATH=. uv run pytest tests/test_sandbox_audit_middleware.py -v
python3 backend/tests/test_no_cross_references.py
```

## Core Features

### Skills & Tools

27 built-in agent tools. 27 public skills. Extensible via MCP servers and custom skills.

| Category | Skills |
|---|---|
| Research | deep-research, academic-paper-review, github-deep-research, systematic-literature-review |
| Code | code-reviewer, code-documentation, qa-tester, **security** |
| Data | data-analysis, chart-visualization |
| Content | newsletter-generation, ppt-generation, podcast-generation |
| Media | image-generation, video-generation, music-generation |
| Design | frontend-design, web-design-guidelines |
| Deploy | vercel-deploy-claimable |
| Trading | pine-script (TradingView Pine Script v5/v6) |
| Business | consulting-analysis |
| Meta | skill-creator, find-skills, bootstrap |

Skills load progressively — only when needed. Slash activation: `/skill-name task`.

### Trading & Market Data

| Tool | What it does |
|---|---|
| `get_ohlcv` | OHLCV candles — yfinance (equities/FX/futures), ccxt (crypto) |
| `compute_indicators` | SMA, EMA, RSI, MACD, ATR, ADX/DMI, Bollinger, VWAP |
| `backtest_signals` | Replays entry signals → win rate, expectancy, profit factor, max drawdown |

No API keys required. `cd backend && uv sync --extra trading`.

### Claude Code Integration

```bash
npx skills add https://github.com/bytedance/deer-flow --skill claude-to-deerflow
```

Send tasks, check status, manage threads — all from Claude Code.

### Sub-Agents

Lead agent spawns sub-agents on the fly — scoped context, tools, termination conditions. Parallel when possible. Structured results synthesized into coherent output.

### Long-Term Memory

Persistent memory across sessions. Profile, preferences, writing style, technical stack. Stored locally, under your control.

### Runs on AMD Compute

Nova serves inference on **AMD Instinct** GPUs — built for the **AMD Developer Hackathon (Act II)**.

- **Fireworks AI** (managed, AMD Instinct MI300X) and **AMD Developer Cloud** (vLLM on ROCm) as one-click presets.
- `GET /api/models/amd-usage` returns machine-readable AMD-usage summary.
- Full setup: [docs/AMD_INTEGRATION.md](./docs/AMD_INTEGRATION.md).

## Sandbox

Three modes:

| Mode | Isolation | Use case |
|---|---|---|
| **Local** | Per-thread dirs, host bash disabled by default | Development |
| **Docker** | Container-based, resource caps | Recommended |
| **Kubernetes** | Pods via provisioner service | Production |

### Sandbox Image Chain

| Layer | Tag | Content | Size |
|---|---|---|---|
| 1 | `nova-sandbox-base` | Upstream image, **pinned by digest** | 10.2 GB |
| 2 | `nova-sandbox-tools` | pandoc, tesseract, Go, Rust, uv, pnpm, Playwright, psql, redis-cli | 18.5 GB |
| 3 | `nova-sandbox-dind` | Nested Docker daemon (`--privileged`) | 18.8 GB |
| 4 | `nova-sandbox-android` | OpenJDK 17, Android SDK, Gradle, Kotlin | 20.4 GB |

```bash
make sandbox-image    # build the chain
```

### Resource Limits

| Limit | Default | Purpose |
|---|---|---|
| Memory | 12g | Prevent host OOM |
| PIDs | 2048 | Fork bomb protection |
| CPU shares | 512 | Host starvation prevention |
| /dev/shm | 1g | Chromium render stability |
| Idle timeout | 3600s | Reclaim unused sandboxes |
| Max lifetime | null | Wall-clock ceiling (configurable) |

## IM Channels

7 platforms. No public IP required for any.

| Channel | Transport |
|---|---|
| Telegram | Bot API (long-polling) |
| Slack | Socket Mode |
| Discord | Gateway WebSocket |
| Feishu / Lark | WebSocket |
| DingTalk | Stream Push (WebSocket) |
| WeChat | Tencent iLink (long-polling) |
| WeCom | WebSocket |

Commands: `/new`, `/status`, `/models`, `/memory`, `/help`.

## Tracing

**LangSmith** and **Langfuse** — both can run simultaneously. Trace correlation fields: `session_id`, `user_id`, `trace_name`, `tags`.

```bash
# LangSmith
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxx

# Langfuse
LANGFUSE_TRACING=true
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
```

## Recommended Models

Model-agnostic. Best with: long context (100k+), reasoning, multimodal, strong tool-use.

## Embedded Python Client

```python
from deerflow.client import DeerFlowClient

client = DeerFlowClient()
response = client.chat("Analyze this paper", thread_id="my-thread")

for event in client.stream("hello"):
    if event.type == "messages-tuple" and event.data.get("type") == "ai":
        print(event.data["content"])
```

## CI/CD & Testing

| Suite | Count | Command |
|---|---|---|
| Backend unit tests | 7,132 | `cd backend && make test` |
| Frontend unit tests | 776 | `cd frontend && pnpm test` |
| Playwright E2E | 143 | `cd frontend && pnpm test:e2e` |
| Blocking IO gate | 19 | `cd backend && make test-blocking-io` |

```bash
make ci          # full local CI
make ci-fast     # lint + tests only
make self-audit  # full self-probe
```

18 GitHub Actions workflows. Pre-commit hooks. CodeQL weekly + every PR.

## Contributing

1. Fork → feature branch
2. `make setup` (Docker) or `make install` (local)
3. Changes with hot-reload
4. `cd backend && uv run pytest` && `cd frontend && pnpm test`
5. PR — CI runs format, lint, typecheck, tests

## Documentation

- [Contributing Guide](CONTRIBUTING.md)
- [Configuration Guide](backend/docs/CONFIGURATION.md)
- [Architecture Overview](backend/CLAUDE.md)
- [CI/CD Pipeline](docs/CI_CD.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Local Models](docs/LOCAL_MODELS.md)
- [Voice System](docs/VOICE.md)
- [Security Architecture](docs/SECURITY.md)

## License

DeerFlow foundation: [MIT License](./LICENSE). All upstream copyright notices preserved.

Nova-specific additions (Agent's Computer UI, sandbox image, watchdogs, **security arsenal**, branding): **proprietary** — see [NOTICE.md](./NOTICE.md). Commercial license terms: `alilabsx@gmail.com`.

## Acknowledgments

Built on [DeerFlow](https://github.com/bytedance/deer-flow) by ByteDance. Powered by [LangChain](https://github.com/langchain-ai/langchain) and [LangGraph](https://github.com/langchain-ai/langgraph).

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Jahanzaib211/nova&type=Date)](https://star-history.com/#Jahanzaib211/nova&Date)
