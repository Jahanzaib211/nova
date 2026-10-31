# Phase 1 — README claims vs reality

Verdict legend: ✅ TRUE · 🟡 PARTIAL · ❌ FALSE · ❔ UNVERIFIABLE here.

| # | Claim | Evidence | Verdict |
|---|---|---|---|
| 1 | 27 built-in agent tools | `tools/tools.py:42` `BUILTIN_TOOLS` = **27** static entries (AST-counted). Note: 2 are self-documented as historically-dead-but-now-bound (`igino_research_tool`, `register_external_dev_server_tool`, tools.py:57-73). | ✅ |
| 2 | 27 public skills | `skills/public/` = **27** dirs, all have `SKILL.md`. | ✅ |
| 3 | 7 IM channels (Telegram/Slack/Discord/Feishu/DingTalk/WeChat/WeCom) | `backend/app/channels/` has all 7 modules. | ✅ |
| 4 | 28 middleware modules, 23 live | `agents/middlewares/*.py` (excl __init__) = **28**. "23 live" not re-verified. | 🟡 |
| 5 | AuthMiddleware fail-closed | `backend/app/gateway/auth_middleware.py:1` "fail-closed safety net"; `app.py:399`. Depth in Phase 5. | ✅ (present) |
| 6 | GuardrailMiddleware fail-closed default | `guardrails/middleware.py:29-31,68-69,91-92` `fail_closed` → `GuardrailDecision(allow=False)`. Default value checked in Phase 5. | ✅ (present) |
| 7 | SandboxAudit "100% recall / 0% FP" | Measured in `test_sandbox_audit_middleware.py:704-715` but only vs a **28-item curated corpus** in the same file → circular. | 🟡 → CLAIM-002 |
| 8 | 80+ security tools / red-team arsenal (Metasploit, Ghidra, Sliver, Empire, bloodhound, NetExec, Volatility, radare2, ZAP, OpenVAS, Wazuh, Falco, Snort…) | Image (`Dockerfile.tools:314-318`) installs ~30 real tools (nmap, sqlmap, nikto, nuclei, gitleaks, trufflehog, hydra, john, hashcat, aircrack-ng, suricata, lynis…). The heavy tools grep to **0** in the image; `/mnt/security-toolkit` is the owner's **markdown KB** (docs, no executables). semgrep/enum4linux/SecLists explicitly dropped. | ❌ for the marquee tools → CLAIM-001 |
| 9 | 4-layer sandbox image chain, base pinned by digest, 10.2→20.4 GB | `Dockerfile.base:17` `FROM …@sha256:742062f99915…` (real digest pin). tools/dind/android chain from `${BASE}` ARG. Sizes not re-measured (images not built — safety). | 🟡 (digest ✅, sizes ❔) |
| 10 | 14-probe self-healing watchdog | `healthcheck-daemon.py` build_probe_factories = **12 registered** probes (P1–P14 numbering but P1_nginx,P2_gateway,P3_frontend,P4_local_llm_gateway,P5_llama_loopback,P6_llama_vram,P7_containers,P8_binary_attestation,P9_bridge,P10_litellm,P11_dify,P12_tunnel,P13_drift,P14_searxng → factory list has 12 tuples; two probe fns exist but check). 14 probe functions defined; **12 wired into the cycle**. | 🟡 → verify in Phase 7 |
| 11 | GET /api/models/amd-usage | `routers/models.py:202-207` route + `amd_usage()` handler. | ✅ |
| 12 | Path-traversal protection (`../`, backslash, bare root, `cd /`, env escapes, `file://`, brace expansion) | Guards live in `sandbox/*.py`; enumerated & bypass-tested in Phase 5. | ❔→P5 |
| 13 | Test counts (README 152+53 files/38,782 lines; VS_DEERFLOW 418; AUDIT 6,928/6,430) | Measured: 401 `test_*.py`, 6,900 backend passed + 689 frontend. Docs inconsistent with each other and with reality. | 🟡 → CLAIM-003 |
| 14 | Free models via LiteLLM (MiniMax M3, Nemotron, Qwen3 Coder, GPT-OSS) | `pm2 nova-litellm` online, `docker/litellm/config.yaml`. Model set not line-checked. | ❔ |

## Headline gaps
- **CLAIM-001 (P1):** the offensive "arsenal" is mostly documentation. The image has a real but smaller scanner set; the C2/RE/AD tools named in the tables are not runnable in-sandbox.
- **CLAIM-002 (P2):** the flagship detection metric is a curated-corpus regression assertion, not a validated real-world rate.
- **CLAIM-003 (P3):** test counts disagree across three docs and none matches the measured numbers → motivates a docs-claim CI gate (Phase 12).
