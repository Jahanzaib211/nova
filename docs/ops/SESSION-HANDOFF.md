# Nova — Session Handoff & Gap Register

> **Historical document** — snapshot as of 2026-08-06. Test counts and gap
> references may be stale. For current status see `docs/AUDIT.md` and
> `README.md`.

**Date:** 2026-08-06 · **Branch:** `main` · **Last commit:** `5f37b642`
**Status:** all suites green — backend 6430 · frontend 565 · e2e 73 · ruff clean

This document exists so the next session can resume with zero context loss. It
covers (1) work in flight, (2) a full-stack gap audit with evidence, and (3) a
dependency-ordered plan where each item states what it unlocks.

Every gap below was verified by reading the code or probing the running system —
none are generic best-practice suggestions.

---

## 0. URGENT — Secrets exposed in the working transcript

Exposed by my own diagnostic commands. Rotate before anything else.

| Secret | How it leaked | Action |
|---|---|---|
| `FIREWORKS_API_KEY` | `docker compose config` rendered `.env` | Rotate at fireworks.ai |
| Cloudflare tunnel token — **nova** | `pgrep -af` printed `--token` | Zero Trust → Networks → Tunnels → Configure → Regenerate |
| Cloudflare tunnel token — **whmcs** | same | same |
| Cloudflare tunnel token — **fox-hosting** | same | same |

Tunnel rotation procedure is documented in `~/.cloudflared/nova-config.yml`:
paste the new token into `/etc/cloudflared/env`, then
`sudo systemctl restart cloudflared-nova`.

**Lesson for future sessions:** never run `docker compose config`, `pgrep -af`,
or `env` without a redacting filter. Use
`sed -E 's/[A-Za-z0-9_-]{40,}/<redacted>/g'`.

---

## 1. Work in flight (voice)

Approved plan lives at `~/.claude/plans/i-want-you-to-sparkling-crown.md`.

### Committed and green (`5f37b642`)

- **Silero VAD was deaf.** Its ONNX graph has dynamic axes, so a wrong-shaped
  input returns a meaningless probability instead of raising. Nova fed it raw
  20 ms frames (320 samples) with no context prefix. Correct contract:
  **512-sample windows + the previous window's last 64 samples prepended**
  (576 wide). Measured: p=1.00 with the prefix, p≤0.06 without, on identical
  audio. Fixed in `speech/vad.py`; pinned hermetically by
  `TestSileroInputContract` in `tests/test_voice_session.py`.
- **CSP had no `media-src`.** It falls back to `default-src 'self'`, and a
  `blob:` URL is not `'self'` — so synthesized audio was refused on every
  deployment and only there (local dev serves no CSP). Fixed in
  `docker/nginx/nginx.conf` + `k8s/charts/nova/files/nginx.conf`.
- `POST /api/voice/speak` — one-shot text→WAV, auth-gated, length-capped.
- Spoken login greeting (`frontend/src/core/voice/greeting.ts`).
- `docker/docker-compose.voice.yaml` overlay; parallel-range model downloader.

### Uncommitted, working, tested (19 new tests passing)

- `speech/devices.py` — **new.** `device: auto|cuda|cpu` resolution for both
  runtimes. `auto` picks silently; explicit `cuda` that falls back warns
  **once, loudly**. This asymmetry is deliberate: silent degradation is exactly
  what hid the Silero bug.
- `speech/engines/kokoro_tts.py` — builds its own `ort.InferenceSession` and
  uses `Kokoro.from_session()`.
  **Why:** `kokoro_onnx` probes `importlib.util.find_spec("onnxruntime-gpu")` —
  a *distribution* name, not a module name. Hyphens are illegal in module names,
  so it never matches and Kokoro stays on CPU **even when GPU is installed**.
- `speech/engines/faster_whisper_stt.py` — `device`/`compute_type` resolved at
  load, with a CUDA→CPU fallback because CTranslate2 reports a CUDA device from
  the driver alone but links its own CUDA 12 runtime.
- `tests/test_speech_devices.py` — **new**, 19 tests, no GPU required.

### GPU — resolved, with measurements

`nvidia-cublas-cu12` + `nvidia-cudnn-cu12` landed (**2.3 GB** under
`site-packages/nvidia`). ctranslate2 4.8.1 links `libcublas.so.12` while the
host has CUDA 13.2, so these wheels are what bridge the gap.

**Whisper `base`, 5 s clip, measured on this box:**

| Device | Compute | Infer | RTF | vs CPU |
|---|---|---|---|---|
| cpu | int8 | 3.158 s | 0.632 | — |
| **cuda** | **float16** | **0.105 s** | **0.021** | **~30× faster** |

`speech/devices.py::preload_cuda_libraries()` makes this work with **no
`LD_LIBRARY_PATH`**. The wheels install CUDA under `site-packages/nvidia/*/lib`,
which is not on the linker's search path — and `LD_LIBRARY_PATH` cannot be fixed
from inside Python because the linker reads it at *exec* time. Loading each
library with `RTLD_GLOBAL` first has the same effect and needs no environment,
so uvicorn, pytest and the container CMD all get it for free. Verified: 12
libraries preloaded, `device=cuda compute=float16` chosen automatically.

**Kokoro — the int8 build was the real problem, not the device.**

`onnxruntime-gpu` 1.28.0 now exposes `CUDAExecutionProvider`. Two bugs had to be
fixed before Kokoro could use it: the ONNX path never called the preload at all,
and onnxruntime dlopens cuDNN by its **unversioned** name `libcudnn.so` while
the wheel ships `libcudnn.so.9` — a resident `.so.9` does not satisfy that,
because dlopen resolves by filename, not by what is loaded.
`onnxruntime.preload_dlls()` handles it; the ctypes pass stays for CTranslate2,
which has no equivalent.

With that fixed, the measurement overturned an earlier decision in this session:

| Kokoro model | Device | RTF | First audio |
|---|---|---|---|
| int8 | cpu | 2.163× | 2815 ms |
| int8 | cuda | 2.156× | 2851 ms |
| **fp32** | **cpu** | **0.392×** | **510 ms** |
| **fp32** | **cuda** | **0.154×** | **200 ms** |

**int8 is 5.5× slower than fp32 on the same CPU** — the opposite of what
quantization is meant to buy, and it made the GPU nearly useless too (~547
Memcpy nodes inserted, because most int8 ops have no CUDA kernel and bounce back
to the host). I had chosen int8 earlier to save 222 MB of disk; it was costing
5.5× throughput.

Net: **first audio 2815 ms → 200 ms (14×)**. And fp32-on-CPU alone gives
2815 ms → 510 ms, so **the k3s deployment benefits without any GPU**.

Defaults flipped to fp32 in `scripts/fetch-voice-models.sh`,
`scripts/docker.sh`, `docker/docker-compose.voice.yaml`, `.env`, and the
real-engine test's model preference order.

### Sandbox outage — codified

The "Docker daemon unreachable" failure mode that kept recurring had the same
shape every time: the gateway container was started *without* the DooD overlay,
so `unix:///var/run/docker.sock` was not mounted inside it. The container was
healthy, the host daemon was fine, but AIO sandboxes could not spawn.

Recovery now lives in `scripts/docker.sh` (called via `make docker-status` /
`make docker-start`):

- `start()` always appends `docker-compose.dood.yaml` when `sandbox.use`
  resolves to `deerflow.community.aio_sandbox:AioSandboxProvider` and
  `provisioner_url` is empty. There is no longer a code path that brings the
  gateway up in `aio` mode without the socket bind.
- `status()` (new) prints four signals in one shot: host socket present,
  gateway container running, socket mounted inside the gateway, gateway
  health probe. If any link is broken it names the missing piece and the
  exact recovery command. Use this as the first check after any outage
  report; it removes the "looks healthy but tools all 502" ambiguity.
- The host script refuses to start in `aio` mode when the host socket is
  missing at all — `start()` exits 1 instead of bringing the stack up in a
  permanently broken state.

If you see "sandbox is down" again, the canonical one-liner is:

```bash
make docker-status    # tells you exactly which link is broken
make docker-start     # idempotent: appends DooD overlay, force-recreates gateway
```

### Voice settings panel (P3) — done

`Settings → Voice`. Backend `GET/PUT/DELETE /api/voice/config` plus
`POST /api/voice/transcribe`; frontend `voice-settings-page.tsx` +
`core/voice/config.ts` + `core/voice/capture.ts`.

Three decisions worth keeping:

- **Writes never touch `config.yaml`.** They go to
  `$DEER_FLOW_HOME/voice-settings.yaml` and are layered over the `speech:`
  section one level deep. Rewriting `config.yaml` would need either `yaml.dump`
  (destroys all comments) or a round-trip YAML dependency for one endpoint.
- **`save_overrides()` calls `reset_engines()`.** Engines are process
  singletons — without it the panel writes a file, reports success, and keeps
  using the engine built at startup. Negative-tested: removing the reset fails
  exactly two tests in `tests/test_voice_settings.py`.
- **The mic test records through the same AudioWorklet the live session uses**
  (`core/voice/capture.ts`) and uploads WAV PCM16. MediaRecorder's WebM would
  need ffmpeg — which is in the Dockerfile but **absent from the running dev
  image**, i.e. it would fail exactly where a diagnostic matters most.

The panel shows `device_requested` vs `device_actual` and measured RTF, so a
`cuda` request that silently fell back, or synthesis slower than playback, is
visible instead of mysterious.

### A trap this session hit: `git add -A`

`graphify-out/` (129 MB of generated knowledge graph) was swept into a commit.
It is **intentionally committed** per the note in `.gitignore`, so it was not
removed — but it broke `tests/test_no_cross_references.py`, because the graph
indexes this repo *including the guard's own docstring*, producing a
self-referential false positive. Fixed with a `SKIP_PREFIXES` entry for
generated output; negative-tested to confirm the guard still catches real
violations.

### The measurements that reprioritised everything

| Stage | Measured (CPU) | Note |
|---|---|---|
| VAD silence wait | 600 ms | config |
| STT whisper-base int8 | 1.16 s | RTF 0.21 |
| **TTS Kokoro first audio** | **2.00 s short / 4.44 s long** | **RTF 1.12–1.51 — slower than real time** |

**TTS is the bottleneck, not STT** — the opposite of my initial assumption.
Kokoro is reported at ~210× real-time on GPU. The fix is the model Nova already
has, on the GPU it already has.

---

## 2. Machine inventory — what is already running and unused

Probed live. All are on `127.0.0.1` unless noted.

| Service | Port | Version | Nova uses it? |
|---|---|---|---|
| **pgvector** | 5438 | `pgvector 0.8.6` on pg16 | ❌ Nova has no vector search at all |
| **SearXNG** | 8090 | JSON API → HTTP 200 | ❌ integration exists, unconfigured |
| **llama.cpp** | 18082 | a local 9B GGUF, 16k ctx, **on GPU** | ❌ |
| Meilisearch ×2 | 7700 / 7701 | v1.11 | ❌ |
| MinIO ×2 | 9010 / 9020 | latest | ❌ artifacts are on local disk |
| Redis ×4 | 6380–6382 | 7 / 8-alpine | ❌ not configured |
| GVM/OpenVAS | 9390–9392 | — | ❌ |

**Hardware:** RTX 3060, 12 GB (**6.0 GB free** — llama.cpp holds 5.4 GB),
CUDA 13.2, 12 cores, 30 GB RAM, 80 GB disk. Docker GPU passthrough **verified
working** (`nvidia` runtime registered; a test container saw the GPU).

**Doc tooling present:** `python-docx`, `libreoffice`/`soffice`. No `pandoc`.

---

## 3. Gap register

Ordered by dependency. **Tier N cannot start until Tier N−1 lands.**

### Tier 0 — Foundations (unlock everything downstream)

#### G1. GPU runtime not installed → *unlocks G4, G8, and voice P1/P2*

- **Evidence:** `onnxruntime` 1.20.1 reports only
  `['AzureExecutionProvider', 'CPUExecutionProvider']`. ctranslate2 wants
  `libcublas.so.12`; host has CUDA 13.2.
- **Fix:** finish `nvidia-cublas-cu12` + `nvidia-cudnn-cu12`; swap
  `onnxruntime` → `onnxruntime-gpu` in the `voice` extra
  (`backend/packages/harness/pyproject.toml`), forwarded from the root
  `backend/pyproject.toml` — *the same forwarding gap that broke
  `uv sync --extra voice` before.*
- **Unlocks:** Kokoro GPU (2.0 s → <100 ms), Whisper GPU, GPU embeddings, GPU OCR.

#### G2. SQLite backend blocks pgvector → *unlocks G4, G5*

- **Evidence:** `config.yaml:251` → `backend: sqlite`.
- **Fix:** add a Postgres profile pointing at the **already-running**
  `apex-seo-postgres` (`127.0.0.1:5438`, role `apex_seo`) — or a dedicated
  `nova` database on it. Add `psycopg[binary]` + `pgvector`.
  `database` is in `STARTUP_ONLY_FIELDS` → **requires a gateway restart**.
- **Note:** Nova already has Alembic migrations under
  `persistence/migrations/`, so this is a config + migration job, not new code.

### Tier 1 — Zero-dependency quick wins (do these first; nothing blocks them)

#### G3. `web_fetch` is on Jina, which 401s without a key

- **Evidence:** `config.yaml:85` → `deerflow.community.jina_ai.tools:web_fetch_tool`.
  This is the failure Nova reported in its **own self-audit** this session.
  A keyless fallback was added, but the primary is still a keyed cloud service.
- **Fix:** point search/fetch at the running SearXNG. Nova already ships
  `deerflow/community/searxng/` — this is config only.

  ```yaml
  # config.yaml:81-85
  - use: deerflow.community.searxng.tools:web_search_tool
    # SEARXNG_BASE_URL=http://127.0.0.1:8090  (set in .env)
  ```

- **Value:** deletes a paid dependency and an entire class of runtime failure.

#### G4. Every memory extraction + title burns cloud tokens

- **Evidence:** `config.yaml:239` `memory.model_name: null`, `config.yaml:210`
  `title.model_name: null` → both fall through to the main model
  (`minimax-m3`, `config.yaml:218`).
- **Fix:** register the **already-running** llama.cpp as an OpenAI-compatible
  model and point the background tasks at it:

  ```yaml
  models:
    - name: local-llama
      use: langchain_openai:ChatOpenAI
      model: <the alias llama.cpp reports at /v1/models>
      base_url: http://127.0.0.1:18082/v1
      api_key: "not-needed"
  memory:  { model_name: local-llama }
  title:   { model_name: local-llama }
  ```

- **Caveat:** the local model's quality for *structured fact extraction* is unverified.
  Gate on a comparison run before switching permanently.

#### G5. Redis support exists but is unconfigured → single worker, volatile runs

- **Evidence:** `packages/harness/pyproject.toml:75` declares a `redis` extra;
  `runtime/stream_bridge/redis_provider.py`, `runs/distributed_lock.py` and
  `runs/cancel_signal.py` all implement it — but `stream_bridge` **does not
  appear in `config.yaml` at all**, so the in-memory default is used.
- **Fix:** `uv sync --extra redis`, then add a `stream_bridge` section pointing
  at a running Redis (`127.0.0.1:6380`). Also `STARTUP_ONLY_FIELDS` → restart.
- **Unlocks:** multiple gateway workers, and run state surviving a restart.

### Tier 2 — Retrieval (requires G1 for speed, **G2 for storage**)

#### G6. Memory recall is ranked by confidence, never by relevance ⚠️ **biggest win**

- **Evidence:** `agents/memory/prompt.py:380`

  ```python
  ranked_facts = sorted(..., key=lambda fact: _coerce_confidence(...), reverse=True)
  ```

  There is no query parameter anywhere in the injection path. Combined with
  `max_facts: 100` (`config.yaml:240`) and `max_injection_tokens: 2000`
  (`config.yaml:243`), Nova **forgets by design** and injects its most-confident
  facts about *anything* regardless of what you asked.
- **Verified absent:** 0 files matching qdrant/chroma/lancedb/pgvector/faiss/rerank.
  The 3 hits for "vector"/"embedding" are unrelated senses of the words.
- **Fix:** `fastembed` (**ONNX, no torch** — same constraint the voice stack
  already respects) + pgvector. Embed facts on write; retrieve top-k by cosine
  similarity to the current turn, then fill the remaining budget by confidence.
- **Requires:** G2. **Unlocks:** G7, G8.

#### G7. No document/workspace RAG

- Nova can read files it is told about, but cannot answer "where did we handle
  X" across a large workspace semantically. `workspace/` provides AST/symbol
  search — lexical, not semantic.
- **Fix:** chunk + embed workspace and uploads into the same pgvector store.
- **Requires:** G6.

#### G8. No reranker → retrieval quality plateaus

- **Fix:** a small ONNX cross-encoder over the top ~50 candidates. Cheap on GPU.
- **Requires:** G6 (nothing to rerank before that).

### Tier 3 — Independent capability gaps

#### G9. No OCR — scanned documents are invisible

- **Evidence:** 0 hits for ocr/docling/tesseract/paddleocr. `markitdown` handles
  digital PDFs only; a photographed invoice yields nothing.
- **Fix:** `rapidocr-onnxruntime` (ONNX, reuses the existing runtime) or IBM
  `docling` (MIT, layout + tables). Hook into `CONVERTIBLE_EXTENSIONS` in
  `utils/file_conversion.py` — the same hook the voice plan uses for audio.
- **Feeds:** G7 (RAG over scanned documents).

#### G10. 6430 tests, none testing whether Nova is *good*

- `tests/test_replay_golden.py` is deterministic replay — it catches
  serialization drift, not answer quality. A prompt change can silently degrade
  the agent with every test still green.
- **Fix:** Langfuse is **already wired** (`deerflow/tracing/`) and has datasets +
  scoring. Largely wiring, not a new dependency. `promptfoo` if you want it in CI.

#### G11. Artifacts on local disk only

- MinIO (S3-compatible) is running on 9010/9020, unused. Matters for durability
  and multi-node; not urgent single-node.

#### G12. Frontend observability + long-thread performance

- **Evidence:** 0 hits for react-virtual/react-window/virtuoso, sentry,
  web-vitals, PWA. Error boundaries exist (5 files).
- **Fix:** virtualize the message list before very long threads become janky;
  add web-vitals if you want real user metrics. Low priority.

---

## 4. Recommended execution order

```
  ┌─ Tier 1 (no dependencies — do first, ~1 session)
  │    G3  SearXNG search .......... config only
  │    G4  local model for memory/title ... config only
  │    G5  Redis stream bridge ..... config + extra
  │
  ├─ Tier 0 (foundations, run in parallel with Tier 1)
  │    G1  GPU runtime ............. unblocks voice P1/P2 + fast embeddings
  │    G2  Postgres/pgvector ....... unblocks all retrieval
  │
  ├─ Tier 2 (needs G1 + G2)
  │    G6  semantic memory ......... ⚠ biggest single improvement
  │    G7  workspace/document RAG
  │    G8  reranker
  │
  └─ Tier 3 (independent)
       G9  OCR      G10 evals      G11 MinIO      G12 frontend
```

**Finish the approved voice plan (P1–P7) alongside Tier 0/1** — it is already
half-implemented and blocked only on G1.

---

## 5. Verification (must stay green throughout)

```bash
# Backend — baseline 6430 passed / 0 failed
cd backend && PYTHONPATH=. uv run pytest tests/ -q
DEERFLOW_VOICE_MODEL_DIR=~/.cache/nova/voice PYTHONPATH=. \
  uv run pytest tests/test_voice_engines_real.py -v     # 10, none skipped
uv run ruff check app packages tests

# Frontend — baseline 539 unit / 73 e2e
cd frontend && pnpm test && pnpm typecheck && pnpm lint
E2E_PORT=3111 CI=1 pnpm exec playwright test --project=chromium

# Infra
docker run --rm -v "$PWD/docker/nginx:/n:ro" nginx:alpine \
  sh -c "mkdir -p /var/log/nova && nginx -t -c /n/nginx.conf"
helm template nova k8s/charts/nova -f k8s/charts/nova/values-staging.yaml >/dev/null
```

### Environment traps that cost time this session

- `.env` exports `DEER_FLOW_CONFIG_PATH` / `DEER_FLOW_EXTENSIONS_CONFIG_PATH`
  into every `uv run`. **These now hold HOST paths** (they were container paths
  when this was first written, hence the inverted advice that used to live
  here). Host-side `uv run` therefore works as-is; it is the *containers* that
  must override them, and any compose service that does not pin the container
  path in its `environment:` block inherits the host path via `env_file:` and
  dies at import with `FileNotFoundError`. That was the 2026-08-17 gateway
  outage — see `docs/TROUBLESHOOTING.md`.

  The host paths are not incidental: `docker-compose.yaml` uses
  `${DEER_FLOW_CONFIG_PATH}` as the *source* of a bind mount, so a container
  path in `.env` would break the prod stack. Don't "fix" `.env`; fix the
  compose service.
- Playwright: port 3000 is taken by an unrelated project and
  `reuseExistingServer` will happily test *it*. Always `E2E_PORT=3111 CI=1`.
- `nginx -t` in a bare container fails on a missing `/var/log/nova`; `mkdir -p`
  first or you get a false negative.
- The gateway is a **container** (`deer-flow-gateway`); host `:8001` is not it.
  Use `docker exec … /app/backend/.venv/bin/python` for in-container checks.
- Auth is **on** (production env), so `/api/*` returns 401 to plain curl.

---

## 6. Design rules learned here — apply to all future work

1. **Silent fallbacks hide catastrophic bugs.** The deaf VAD, the always-CPU
   Kokoro, and the lying `/api/voice/status` were all silent degradations that
   kept tests green. Any fallback must warn once, loudly.
2. **Dynamic-axis ONNX models accept wrong-shaped input and return garbage.**
   Assert expected shape explicitly; add a hermetic test that pins the tensor
   width so CI catches it without weights.
3. **Measure before prioritising.** I spent effort on STT when TTS was 2× worse.
4. **Deployment-only traps are invisible on `make dev`**, which serves no nginx
   headers. CSP `media-src`, `Permissions-Policy`, and WS `Upgrade` all bit
   here. `tests/test_nginx_preview_headers.py` is the guard — extend it.
5. **Negative-test every guard.** Break the fix, confirm exactly the intended
   tests fail, restore. Several "passing" tests were passing vacuously.

### Frontend / dev-server regression sweep — 2026-08-14

A second sweep of failures surfaced while exercising the Browser tab:

1. **UI "substep failed and finished" misclassification** —
   `frontend/src/core/threads/activity.ts` treated every `ToolMessage` whose
   content started with the literal substring `"Error:"` as an error. 40+
   sandbox tools (bash policy refusal, search_files/read_file missing-target
   returns, etc.) legitimately return `f"Error: …"` on success paths. The
   heuristic painted every such step red and dropped the running event out of
   the "running" set. Fix: trust `ToolMessage.status` only (matches the
   upstream LangGraph SDK). Tests inverted the wrong-direction assertion and
   added four success cases (`bash_tool` policy refusal, `search_files`,
   `read_file`, `grep_files`).
2. **Terminal tab empty for non-bash runs** — `TERMINAL_TOOLS` was
   `{bash, execute_command, search_files, grep_files}`. A run that only
   edited files (write_file / str_replace / read_file) left the tab empty.
   Fix: extended the set, plus added `terminalOutputClass(status)` helper
   with a regression test that asserts `done` events whose output starts
   with `Error:` render emerald, not red.
3. **Dev server watchdog** — `_start_dev_server_aio` / `_start_dev_server_local`
   returned a handle in `status: "starting"` forever when the child never
   bound a port. Added `_watch_dev_server_start` that flips to a new
   `crashed` state within 12 s. `stop_dev_server` cancels the watchdog.
   `tests/test_dev_server_watchdog.py` (3 cases).
4. **External dev server registration** — agents that started a dev server
   via a raw `bash` tool (e.g. `nohup node /tmp/srv.cjs`) had no way to wire
   the panel to it. Added `POST /api/sandbox/dev-external` (refuses HTTP
   400 if the advertised port isn't actually listening) and
   `register_external_dev_server_tool`. `/dev-status` now returns `host`
   and `absproxy_url` so the Browser tab can fall back.
5. **Browser tab absproxy fallback** — `useDevServerStatus` returns both
   `url` and `absproxyUrl`; `browser-tab.tsx` iframes the canonical
   preview URL and flips to the absproxy URL on iframe `onError`. A small
   amber "Showing via absproxy" badge makes the fallback visible.
6. **Next.js scaffold config** — `scaffold_project_template` was writing
   `next.config.ts`, which Next 14 does not support. Changed to
   `next.config.mjs`; the test pins the choice and refuses to regress to a
   `.ts` config for Next < 15.
7. **Next.js dev-mode chunk rewrite** — `_prefix_html_urls` only rewrote
   `href="/..."`/`src="/..."` attributes. Dev mode emits chunk URLs inside
   the streaming payload `self.__next_f.push([1, "...\"/_next/static/..."])`,
   which the naive replace missed. Test
   `tests/test_next_dev_chunk_rewrite.py` pins the contract (4 cases).
8. **Custom-events wire contract** — `backend/contracts/custom_events_contract.json`
   is the shared schema for `task_progress` / `verify_result` /
   `llm_error` / `task_running`. Backend and frontend parsers will both
   load it; `tests/test_custom_events_contract.py` (6 cases) and
   `tests/test_custom_event_sse_shape.py` (3 cases) pin the shape and the
   SSE frame.
9. **Sandbox observation writer round-trip** — `tests/test_sandbox_observation_writer.py`
   (4 cases) asserts `_write_sandbox_observation` actually appends a JSON
   line for per-thread local, AIO `_thread_sandboxes`, and the legacy
   `local` (skip) paths. Sidesteps the pre-existing circular import in
   `workspace_tools.py` by exec-ing the function source directly.
10. **CDP URL rewriter** — `tests/test_cdp_url_for_gateway.py` (5 cases)
    pins the loopback-to-routable rewrite for the k3s/provisioner path
    that previously silently burned three retries.
11. **Voice real-engine env codify** — `make install` now installs
    `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` so the host venv finds
    `libcublas.so.12` for the real-engine voice tests. `make test-voice`
    sets the right `LD_LIBRARY_PATH`.

### Hermetic gates on this commit

- backend pytest (excluding the flaky `test_sandbox_orphan_reconciliation_e2e`
  which races on shared Docker state): **6422 passed, 23 skipped, 0 failed**
- backend blocking-IO: 19 passed
- backend ruff: all checks passed
- frontend `pnpm test`: **565 passed**
- frontend `pnpm check`: 0 errors
- frontend Playwright mocked: **73 passed, 3 skipped**

Branch: `audit/codify-sandbox-2026-08-14`. Ready to merge to `main`.

---

## 7. Self-probe 2026-08-14 — write_file integrity

**Date:** 2026-08-14 · **Branch:** `main` (c71e4264) · **Status:** all green

The `Novaselfprobe.zip` self-audit output reported F2: `write_file` of 102 810 B
produced only 68 909 B on disk (silent truncation). Investigation:

### Findings

- **`LocalSandbox.write_file`** (local sandbox): rounds-trips all payloads correctly.
  Tested via `tests/test_write_file_no_truncation.py` (8 cases: 102 KB, 200 KB
  threshold, UTF-8 boundary, append, emoji, overwrite).
- **`AioSandbox.write_file`** (Docker sandbox): not yet reproduced. The AIO path
  delegates to `self._client.file.write_file()` which may have different behavior.
- **Tool layer** (`write_file_tool`): auto-chunk logic and byte-count reporting
  already covered by `test_write_file_tool_size_guard.py` (11 cases).

### Actions taken

1. Added `tests/test_write_file_no_truncation.py` — 8 integrity cases.
2. Moved `Novaselfprobe.zip` → `.nova/self-audit/` (gitignored).
3. Committed probe output as `docs/audit/2026-08-14-self-probe.md`.
4. Added `.opencode/skill/nova-self-audit/SKILL.md` + `make self-audit`.
5. Added `docs/ops/README.md`, `docs/audit/README.md`.

### Remaining

- Reproduce F2 on AIO sandbox (requires Docker with DooD overlay).
- Move `backend/scripts/` → `backend/tests/scripts/` ✅ (done this session).
- Move `backend/contracts/` → `contracts/` ✅ (done this session).
