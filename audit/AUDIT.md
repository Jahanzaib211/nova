# Nova — Full-Stack Forensic Audit

**Date:** 2026-09-16 · **Branch:** `regression/2026-08-25-ws-sweep` @ 2feb0fc9 · **Method:** read-only, evidence-cited (`path:line` + quote), tests run natively on the live host (single-process, niced). No `act`, no image builds, no sandbox command execution. Per-phase notes in `audit/notes/`, machine-readable findings in `audit/findings.jsonl`, raw command output in `audit/raw/`.

---

## 1. Executive summary

Nova is a **mature, unusually well-engineered codebase** wrapped in **over-stated marketing** and running in a **dangerous production posture**. The engineering quality is high: 6,900 backend tests + 689 frontend tests all green, `shell=True` banned by construction, a Blockbuster gate for blocking-IO, timing-safe auth comparisons, fail-closed auth/guardrail middleware, proper markdown XSS sanitization, and near-zero real dead code. The problems are not sloppy code — they are **posture, claims, and missing gates.**

**Findings: 1 P0, 9 P1, 12 P2, 7 P3 (29 total).**

### Top 5 risks (plain language)
1. **The agent runs as root on the box that runs everything.** The live sandbox uses the `--privileged` android/dind image (`config.yaml:272`, "Effectively host root"), and subagents share it. The only barrier between agent-generated bash and host root is a **bypassable regex denylist**. This host also runs Postgres, all tunnels, and ~30 other PM2 apps. *(SEC-011 P0, SEC-010 P1)*
2. **Anyone can drive the agent.** IM channels default to allow-all (empty `allowed_users` = everyone), and there is **no per-user cost budget** and no general API rate limit — so an outsider can spend unbounded model tokens and run that privileged sandbox. *(SEC-013, PROD-002)*
3. **The "80+ security tools" arsenal is mostly documentation.** The marquee red-team tools (Metasploit, Ghidra, Sliver, Empire, bloodhound, Volatility…) are in neither the image nor as executables; `/mnt/security-toolkit` is a markdown KB. *(CLAIM-001)*
4. **Schema changes don't ship.** Tables are created with `create_all` at boot; Alembic migrations exist but nothing runs them on deploy, so column changes to existing tables silently never apply. *(PROD-001)*
5. **The advertised security scanning isn't enforced.** CI has CodeQL only; the README's Trivy/Grype/Gitleaks/SBOM are absent, and `pip-audit`/`pnpm audit` run with `|| true`. Next.js ships with 2 critical advisories. *(GATE-SEC, CI-001, DEP-001)*

**Headline recommendation:** the code is ready; the *operating posture* is not. Before anything else, (a) drop the default sandbox to the non-privileged tools layer and gate dind behind an explicit allowlist, (b) default IM channels to deny + add a per-user budget, (c) make the dependency/secret/image scans actually fail CI. These are days of work, not a rewrite.

---

## 2. Baseline snapshot (Phase 0)

| Check | Result |
|---|---|
| Backend pytest (excl. blocking_io) | **6,900 passed, 21 skipped, 1 xfailed** (7m02s, single-process) |
| Backend blocking-IO gate | 20 passed |
| Frontend typecheck / lint | clean / clean (0 warnings) |
| Frontend vitest | **689 passed / 76 files** |
| pnpm audit | **2 critical, 37 high, 39 moderate, 9 low** |
| pip-audit (193 locked prod pkgs) | 1 vuln (langgraph-checkpoint-sqlite) |
| gitleaks (tree + history) | **0 real secrets** (24 history hits are test fixtures) |
| `make doctor` | exit 1 — both errors are host/container false negatives (DOC-001) |
| Coverage gate | measured, **no floor, `app/` excluded** |

Repo: backend/app 27k LOC, harness 71k, backend/tests 125k (401 `test_*.py`), frontend/src 58k. Stack: FastAPI + LangGraph 1.1 embedded in Gateway, Postgres checkpointer + app DB, Next 16 / React 19. Highest churn: sandbox provisioning (backend+frontend) and the agent-computer panel. Full detail: `audit/notes/00-baseline.md`.

---

## 3. Claim-vs-reality (Phase 1)

| Claim | Verdict | Note |
|---|---|---|
| 27 built-in tools | ✅ | `tools.py:42` = 27 (2 self-documented as formerly-dead, now bound) |
| 27 public skills | ✅ | all have `SKILL.md` |
| 7 IM channels | ✅ | all present |
| 28 middleware modules | ✅ | |
| Auth/Guardrail fail-closed | ✅ | present + default-on |
| GET /api/models/amd-usage | ✅ | `routers/models.py:202` |
| **"100% recall / 0% FP" classifier** | 🟡 | measured only vs a **28-item curated corpus in its own test** (CLAIM-002) |
| **"80+ security tools" red-team arsenal** | ❌ | image has ~30 real scanners; marquee C2/RE/AD tools absent; `/mnt/security-toolkit` = docs (CLAIM-001) |
| 4-layer chain "pinned by digest" | 🟡 | only the base `FROM` is digest-pinned; the **running** image is `:latest` (SEC-012) |
| 14-probe watchdog | 🟡 | 14 probe fns defined, **12 wired** into the cycle |
| Test counts (152+53 / 418 / 6,928…) | 🟡 | three docs disagree; none matches measured (CLAIM-003) |

Full table: `audit/notes/01-claims.md`.

---

## 4. Findings by domain

### Security (the highest-value phase — `audit/notes/05-security.md`)
- **SEC-011 (P0)** — privileged android/dind sandbox is the live default; subagent-shared; host-root-equivalent on the box that runs prod.
- **SEC-010 (P1)** — classifier is a regex denylist; documented bypasses include `head /etc/shadow`, `rm -rf /etc`, `rm -rf / --no-preserve-root`, two-step `curl -o /tmp/x && bash /tmp/x`, `nc -e`, `python3 -c` reverse shells. (Paper analysis; nothing executed.)
- **SEC-013 (P1)** — IM channels default allow-all.
- **SEC-014 (P2)** — execution/security audit trail is an in-memory `deque(maxlen=10000)`, not durable; admin audit *is* DB-backed.
- **SEC-012 (P2)** — running image is floating `:latest`.
- **SEC-002 (P2)** — live `.env` duplicated into 2 stray `.kilo/worktrees/*/.env`.
- **SEC-015 (P3)** — JWT no aud/iss/require-exp (alg pinned HS256 — good).
- **SEC-001 (P3)** — gitleaks clean; no CI gitleaks gate / allowlist.
- *Solid:* middleware order, CORS (`*` stripped), CSRF (timing-safe double-submit), BYOK Fernet (env key, never returned plaintext).

### Architecture (`02`), Backend (`03`), Frontend (`04`)
- **ARCH-001 (P2)** — `test_no_cross_references.py` is a sibling-project name gate, not module-isolation.
- **BUG-001/002 (P3)** — 9 naive datetimes (subagent lifecycle); 90 silent `except: pass`.
- **FE-001 (P2)** — `noImplicitAny:false` undercuts `strict:true`.
- *Solid:* SSE hooks guard thread-switch races + per-thread liveness; markdown sanitized via rehypeRaw→rehypeSanitize; only 2 `dangerouslySetInnerHTML` (both over highlighter output).

### Testing (`06`), Production (`07`), Caching/Perf (`08`), API-fit (`09`), Stubs (`10`), Upgrades (`11`)
- **PROD-001 (P1)** — migrations not run on deploy (`create_all` masks drift).
- **PROD-002 (P1)** — no per-user LLM cost budget; rate limit only on auth endpoints.
- **PERF-001 (P2)** — `stream_bridge.type=memory` forces single-process (Redis available, unused).
- **DEP-001 (P1)** — Next 16.2.12 → ≥16.3.3 (2 critical); **DEP-002 (P2)** — langgraph-checkpoint-sqlite.
- **TEST-001 (P2)** — no FE↔BE contract/OpenAPI sync test.
- **PERF-002 / DOC-001 / CLAIM-003 (P3)** — LLM/ETag caching; doctor false-negative; doc count drift.
- *Clean:* concurrency IS tested; skips are legit platform guards; near-zero dead code; timing-safe compares everywhere; no shell-out-where-SDK-exists.

---

## 5. Missing CI gates (Phase 12a)

Present: backend lint/format/tests, blocking-IO, coverage (no floor), cross-ref, frontend lint/typecheck/test/build, CodeQL, Playwright e2e, replay-golden, lighthouse, container build.

Missing → **CI-001** (audits `|| true`), **CI-002** (no coverage floor, `app/` excluded), **GATE-SEC** (gitleaks/Trivy/Grype/SBOM/mutation), **GATE-MIGRATE** (alembic check), **GATE-DOCS** (claim counts), plus bundle-size/TTFT/license/no-network-unit/no-skip-on-main and a **sandbox-escape regression corpus**.

---

## 6. Blast-radius map + baseline-first tests (Phase 12b)

Change-risk ranked; write these regression suites **before** refactoring each:
1. **Classifier + privileged sandbox** → bypass corpus as tests + assert default is non-privileged tools layer.
2. **Streaming pipeline** (`stream_bridge`, `services.py`, FE `sandbox/hooks.ts`) → reconnect/thread-switch/soak; memory→redis parity test.
3. **Graph/state + 28 middlewares** → middleware-order + fail-closed + state-schema.
4. **Auth/guardrail chain** → coverage floor + aud/iss/exp.
5. **Persistence/migrations** → alembic up/down + create_all-vs-migration parity.
6. **IM adapters** → default-deny ACL test.

High fan-in hubs to touch carefully: `runtime.user_context` (51), `config.app_config` (43), `config.paths` (42).

---

## 7. Roadmap & first 10 PRs (Phase 12c)

**P0 / P1 — security & prod blockers (do first)**
1. **PR-1 (SEC-011):** default sandbox → non-privileged tools layer; gate dind behind explicit per-thread opt-in + allowlist. *Baseline test first.* [L, host-wide]
2. **PR-2 (SEC-013):** IM channels default-deny; `allow_all:true` must be explicit. [S]
3. **PR-3 (CI-001 + GATE-SEC):** drop `|| true`; add gitleaks + Trivy(fs+image, fail HIGH/CRITICAL w/ allowlist) + SBOM. [M]
4. **PR-4 (DEP-001):** `pnpm up next@^16.3.3 dompurify mermaid js-yaml minimatch flatted`; re-run audit. [S, covered by vitest+e2e]
5. **PR-5 (PROD-001 + GATE-MIGRATE):** run `alembic upgrade head` on deploy; CI `alembic check`. [M]
6. **PR-6 (PROD-002):** per-user/thread token budget wired to existing credit tables; general API rate limit. [M]
7. **PR-7 (SEC-010):** land the bypass corpus as regression tests; reframe classifier as advisory telemetry. [M]

**P2 — hardening & correctness**
8. **PR-8 (CI-002):** coverage floor + include `app/`; per-module floors for auth/guardrail/classifier. [S]
9. **PR-9 (SEC-012 + PERF-001):** pin sandbox image by digest; flip `stream_bridge`+rate-limiter to Redis behind a parity test. [M]
10. **PR-10 (SEC-014 + SEC-002):** persist the execution audit chain to an append-only table; delete stray `.kilo` worktree `.env` copies + add to sandbox mount denylist. [M]

**P3 — polish (batch later):** BUG-001/002, FE-001, SEC-015, DEP-002, TEST-001, GATE-DOCS, PERF-002, DOC-001, CLAIM-003, ARCH-001, CLAIM-001/002 doc rewrites.

Ordering rationale: PR-1/2/6 shrink the attack surface an outsider can reach; PR-3/4 stop known-bad from merging/shipping; PR-5 prevents the next schema outage; PR-7/8/9/10 make the security *claims* testable and the audit trail durable. Early PRs de-risk later refactors by establishing the baseline suites in §6.

---

## 8. Appendix — commands run

All raw output under `audit/raw/`: `00-pytest.txt`, `00-pytest-blocking-io.txt`, `00-fe-{typecheck,lint,test}.txt`, `00-pnpm-audit.json`, `00-pip-audit.json`, `00-gitleaks-{tree,history}.json`, `00-doctor.txt`, `00-inventory.txt`, `00-stack.txt`, `00-workflows.txt`, `00-churn.txt`, `00-mem-before.txt`. Findings ledger: `audit/findings.jsonl` (29). Per-phase reasoning: `audit/notes/00`–`12`.

**Rules honored:** read-only (only `audit/**` written — verify `git status`); no `act`/image builds/privileged containers; no commands run in any sandbox; secrets masked. Live `pm2 nova` and `deer-flow-gateway` confirmed healthy after the test runs.
