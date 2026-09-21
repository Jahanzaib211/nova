# Phase 11 — Dependency & upgrade assessment

## Security-driven (from Phase 0 audits)
| Upgrade | From → To | Why | Breaking? | Would tests catch regressions? |
|---|---|---|---|---|
| **next** | 16.2.12 → ≥16.3.3 | 2 CRITICAL (DEP-001): Windows RCE (N/A on Linux) + AVIF/sharp Image-Opt RCE | patch-level, low | Playwright e2e + vitest partially; add a build+smoke gate |
| **dompurify / mermaid / js-yaml / minimatch / flatted / brace-expansion** | current → patched | 37 high + 39 moderate; XSS-bypass + ReDoS/prototype-pollution. rehypeSanitize mitigates markdown path, mermaid renders separately | mostly patch | vitest covers rendering; mermaid output not asserted for XSS |
| **langgraph-checkpoint-sqlite** | 3.0.3 → 3.1.1 | PYSEC-2026-3636 (DEP-002); low live exposure (postgres checkpointer active) | patch | replay-golden + checkpoint tests |

## Framework currency
- **Next 16 / React 19 / TS strict** — current majors. Good.
- **Playwright** 1.59 → 1.63, **@tailwindcss/postcss** 4.1→4.3, many **@radix-ui** minors behind — routine minor drift, low risk, batch-upgradeable.
- **Python** pinned `>=3.12` (current). **Node 22 LTS** in CI (`local-ci.yml` NODE:"22") while the dev host runs Node 26.8.1 — CI/host version skew worth aligning.
- **LangChain family**: pins are deliberately ceilinged (harness pyproject comment: newest vuln-free langchain and the rest are "mutually unsatisfiable today"). This is a **known, documented** tension — a langchain 1.3.x with a fixed transitive dep is blocked by other constraints. Track upstream; not fixable by a blind bump.

## Cross-cutting
The single most important upgrade-enabling investment is **CI-001** (make audits fail the build) + **CI-002** (coverage floor incl. `app/`): today an upgrade could regress auth/guardrail coverage or reintroduce a CVE with no signal. Every upgrade above should land behind those gates.

## Findings: DEP-001 (P1), DEP-002 (P2) already filed. No new.
