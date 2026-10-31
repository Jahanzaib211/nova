# Phase 8 — Caching & performance

## Cache inventory
| Cache | Scope | TTL/bound | Multi-worker safe? |
|---|---|---|---|
| SearchCache (searxng) | module singleton | has bound (search_cache.py) | per-worker |
| `_enabled_skills_cache` / `_by_config_cache` (lead_agent/prompt.py:22-23) | module | invalidated on config change | per-worker |
| `_tiktoken_encoding_cache` (memory/prompt.py:192) | module dict | unbounded (few keys) | per-worker, fine |
| `lru_cache` prompt.py:731 (maxsize=32), worker.py:143 (maxsize=128), persistence/base.py `@cache` | function | bounded | per-worker |
| memory `_memory_cache` (memory/storage.py:69) | instance | — | per-instance |
| preflight_quota `_cache` (preflight_quota_middleware.py:76) | instance | tuple TTL | per-instance |
| httpx client pool (memory/updater.py:32 `@lru_cache`) | module | singleton | good (reuse) |
| Redis (stream bridge / distributed lock / cancel / rate limit) | **available but NOT selected** (`type: memory`) → PERF-001 | — | would be shared |

All application caches are **in-process**. Correct today (single Gateway process) but each becomes a per-replica inconsistency the moment you scale — which PERF-001 also blocks.

## Findings
- **PERF-001 (P2):** `stream_bridge.type=memory` (config.yaml:408) forces in-process SSE/rate-limit/lock/cancel. Redis is present in the stack but unused. This is THE thing to change before any horizontal scale.
- **PERF-002 (P3):** no LLM response cache (LiteLLM `litellm_settings` has no cache block); no ETag on read-heavy list GETs (only `artifacts.py:163` sets Cache-Control).

## Recommendations (not implemented)
1. Flip `stream_bridge` + rate limiter to Redis (reuse existing Redis) → unlocks replicas + shared rate windows + cross-replica cancel.
2. LiteLLM exact/semantic cache (Redis) for idempotent skill/tool LLM calls.
3. ETag/If-None-Match on `GET /api/models`, `/api/skills` (read-heavy, rarely change).
4. Sandbox warm pool already exists — measure cold-start contribution to TTFT.
5. Instrument TTFT on the streaming path (correlation_id already emitted first — add a timing frame) to find the top-3 latency contributors.
6. Confirm `events[]` (frontend) and the in-memory audit deque don't grow unbounded on long streams (load/soak test — Phase 6 gap).
