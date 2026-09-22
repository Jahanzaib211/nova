# Phase 4 — Frontend audit

**Overall: well-engineered, with a real XSS defense already in place.**

## Config
- Next 16.2.12, React 19, TS strict. `noUncheckedIndexedAccess: true` (good), but **`noImplicitAny: false`** (tsconfig.json:15) → FE-001. Only 1 explicit `any`/`as any` in `src` and 0 lint warnings, so the annotated surface is clean; the risk is *un*annotated boundaries.

## XSS surface — DEFENDED
- Markdown from untrusted model/tool/web output goes rehypeRaw → **rehypeSanitize** (scoped schema) → rehypeKatex (`core/streamdown/plugins.ts:37-40`, comment "XSS fix, audit A1"). Reasoning/thinking content uses a variant **without** rehypeRaw to stop hallucinated tags rendering.
- `dangerouslySetInnerHTML` only in `code-block.tsx:115,120` — fed syntax-highlighter output over the code *text* (escaped by the highlighter), not raw user HTML. Acceptable.
- ⚠️ Residual: the markdown/diagram libs themselves carry advisories (DEP-001: dompurify, mermaid XSS-bypass, high/moderate). rehypeSanitize mitigates the markdown path; **mermaid** renders separately — confirm mermaid output is sanitized or upgrade it.

## SSE / streaming — well-guarded
- `core/sandbox/hooks.ts`: on `threadId` change resets events + cancels RAF (:203-211); connection effect uses a `cancelled` flag, `esRef.current?.close()`, exponential retry with reset on open (:213-291). No stale-thread event bleed.
- `core/api/stream-liveness.ts`: transport-level liveness **keyed per thread id**, 15s server heartbeats, correlation IDs — distinguishes "idle" from "dead TCP". Mature.
- Events accumulate into `setEvents` batched via requestAnimationFrame (pendingRef) — bounded per render, but confirm long runs don't grow `events[]` unboundedly in memory (Phase 8).

## Server state
- Mixed: `@tanstack/react-query` in 18 files, raw `fetch` in ~40. Not wrong, but inconsistent — some server state hand-rolled with fetch+useEffect (refetch/dedupe left to the component). → Phase 9 (API-fit), not a bug.

## No findings of substance beyond FE-001 and the DEP-001 lib upgrades.
