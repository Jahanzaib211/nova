# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nova Frontend is a Next.js 16 web interface for an AI agent system. It communicates with a LangGraph-based backend to provide thread-based AI conversations with streaming responses, artifacts, and a skills/tools system.

**Stack**: Next.js 16, React 19, TypeScript 5.8, Tailwind CSS 4, pnpm 10.26.2

## Commands

| Command          | Purpose                                             |
| ---------------- | --------------------------------------------------- |
| `pnpm dev`       | Dev server with Turbopack (<http://localhost:3000>) |
| `pnpm build`     | Production build                                    |
| `pnpm check`     | Lint + type check (run before committing)           |
| `pnpm lint`      | ESLint only                                         |
| `pnpm lint:fix`  | ESLint with auto-fix                                |
| `pnpm test`      | Run unit tests with Vitest                          |
| `pnpm test:e2e`  | Run E2E tests with Playwright (Chromium)            |
| `pnpm typecheck` | TypeScript type check (`tsc --noEmit`)              |
| `pnpm start`     | Start production server                             |

Unit tests live under `tests/unit/` and mirror the `src/` layout (e.g., `tests/unit/core/api/stream-mode.test.ts` tests `src/core/api/stream-mode.ts`). Powered by Vitest; import source modules via the `@/` path alias.

E2E tests live under `tests/e2e/` and use Playwright with Chromium. They mock all backend APIs via `page.route()` network interception and test real page interactions (navigation, chat input, streaming responses). Config: `playwright.config.ts`.

**Running E2E locally.** Two gotchas on this box:

- Port 3000 is often taken by an unrelated project, and `reuseExistingServer` is
  on outside CI — so Playwright would happily test _that_ app. Always pass a
  free port: `E2E_PORT=3111 CI=1 pnpm exec playwright test --project=chromium`.
- `playwright install` refuses on Ubuntu 26.04, so the browser build
  `@playwright/test` wants may be missing. If a run fails with
  `Executable doesn't exist`, alias a cached build to the expected revision
  (check `node_modules/.pnpm/playwright-core@*/.../browsers.json` for the number):

  ```bash
  cd ~/.cache/ms-playwright
  ln -sfn chromium_headless_shell-<cached> chromium_headless_shell-<wanted>
  ```

The Agent's Computer panel polls several `/api/sandbox/*` endpoints; without
`mockSandboxAPI` (`tests/e2e/utils/mock-api.ts`) those proxy to a gateway that
isn't running under `pnpm start`, the tabs render empty, and assertions pass
vacuously.

## Architecture

```
Frontend (Next.js) ──▶ LangGraph SDK ──▶ LangGraph Backend (lead_agent)
                                              ├── Sub-Agents
                                              └── Tools & Skills
```

The frontend is a stateful chat application. Users create **threads** (conversations), send messages, and receive streamed AI responses. The backend orchestrates agents that can produce **artifacts** (files/code) and **todos**.

### Source Layout (`src/`)

- **`app/`** — Next.js App Router. Routes: `/` (landing), `/workspace/chats/[thread_id]` (chat).
- **`components/`** — React components split into:
  - `ui/` — Shadcn UI primitives (auto-generated, ESLint-ignored)
  - `ai-elements/` — Vercel AI SDK elements (auto-generated, ESLint-ignored)
  - `workspace/` — Chat page components (messages, artifacts, settings)
  - `landing/` — Landing page sections
- **`core/`** — Business logic, the heart of the app:
  - `threads/` — Thread creation, streaming, state management (hooks + types)
  - `api/` — LangGraph client singleton
  - `artifacts/` — Artifact loading and caching
  - `channels/` — IM channel connections (provider catalog, connect/runtime-config API + hooks)
  - `i18n/` — Internationalization (en-US, zh-CN)
  - `settings/` — User preferences in localStorage
  - `memory/` — Persistent user memory system
  - `skills/` — Skills installation and management
  - `messages/` — Message processing and transformation
  - `mcp/` — Model Context Protocol integration
  - `models/` — TypeScript types and data models
  - `tools/` — Tool-call labelling (`utils.ts`) and per-tool result summarizers (`trading.ts`, which reduces the `trading` group's JSON payloads to a compact stat row)
- **`components/workspace/agent-computer/`** — the Agent's Computer panel (Browser / Files / Terminal / Editor / Activity / Review tabs). Two invariants worth knowing before editing it:
  - **Effects must respect `active` too, not just fetches.** `terminal-tab.tsx`'s auto-scroll called `scrollIntoView({behavior:"smooth"})` on every event with no gate, so a hidden Terminal dragged a hidden subtree and jolted the surrounding panel. The one deliberate exception is the ttyd iframe, which stays ungated because tearing it down would lose the user's shell — that reasoning does not extend to effects.
  - **Every tab stays mounted** and is only `hidden` via CSS (`agent-computer-panel.tsx`). So any tab that fetches, polls, streams or holds a socket **must** take an `active` prop and gate on it. Getting this wrong is invisible in the UI and expensive: a missed gate left an `EventSource` open for the life of the thread (`review-tab`), re-downloaded a whole deliverable every 2s (`browser-tab`), and held a live ttyd PTY and a noVNC video stream while off-screen. Note `refetchIntervalInBackground: false` does **not** help — it only covers a backgrounded browser _window_, not a CSS-hidden subtree.
  - **The file tree's namespace** (`files-tab.tsx::treePathOf`). Workspace files are flattened to the top level while `outputs/` and `uploads/` keep their mount folder, so the workspace's own `outputs/` directory would collide with the `/mnt/user-data/outputs` mount — one file silently overwrote the other while the header counted both. `treePathOf` disambiguates only the genuinely ambiguous paths. A node can also be a file _and_ a folder at once (a file `build` beside `build/out.js`); both branches must render or the subtree vanishes. Covered by `tests/unit/components/workspace/files-tab-dedupe.test.ts`.
  - **Downloads** go through `urlOfArtifact({ download: true })` (`core/artifacts/utils.ts`) → the artifacts endpoint, which streams real bytes, enforces ownership, and sets an RFC 5987 `Content-Disposition`. Do **not** build downloads on `/api/sandbox/file`: it returns JSON and `read_text()`s the file, so it corrupts every binary.
  - **Browser-tab preview URL selection** (`browser-tab.tsx::buildPreviewSrc`). The iframe loads from `devServer.url` first (canonical preview proxy); on iframe `onError` it flips `srcMode` to `absproxy` and uses `devServer.absproxyUrl` (the generic gateway proxy at `/api/sandbox/absproxy/{tid}/{port}/`). This is the safety net for dev servers started outside `start_dev_server` (e.g. an agent launched a `node` server via raw bash). A small amber **"Showing via absproxy"** badge makes the fallback visible. Both URLs come from `/api/sandbox/dev-status`. Covered by `tests/unit/components/workspace/agent-computer/browser-tab-preview-src.test.ts`.
  - **Terminal/Activity partition** (`core/threads/tool-surface.ts`). The two tabs render a _partition_ of one event stream: `isTerminalTool` decides Terminal, `isActivityTool` is its complement, and every tool must land in exactly one. It lived as a bare name list inside `terminal-tab.tsx` and drifted twice — 2026-08-14 (`write_file`/`str_replace`/`read_file` missing, so file-only runs left the tab empty) and 2026-08-21 (the whole `shell_*` family missing, so an agent using the modern AIO execution path showed _nothing_ in Terminal while the backend stream was healthy). The failure is silent by construction: a misclassified event goes to the _other_ tab rather than disappearing, so nothing errors. Hence the `shell_` **prefix** rule — the next member of that family classifies itself. Extracting it also revealed two more consumers of the stale list (`files-tab`'s running count, the panel's terminal badge), so the miscount was wider than the tab. Pinned by `tests/unit/core/threads/tool-surface.test.ts`, which asserts the partition is total and disjoint across the live tool set. `terminalOutputClass(status)` (extracted helper) decides the output block's color; a `done` event whose content starts with `Error:` still renders emerald, because `activity.ts` no longer classifies such events as errors (it trusts `ToolMessage.status`). Covered by `tests/unit/components/workspace/terminal-tab-styling.test.tsx`.
- **`core/voice/`** — full-duplex voice. `state.ts` is a **pure** reducer (idle → listening → thinking → speaking) kept free of I/O so turn-taking is unit-testable without a browser; `playback.ts` is the interruptible queue that makes barge-in possible — audio already handed to the sound device cannot be retracted, so each chunk is its own `AudioBufferSourceNode` and `interrupt()` stops all of them (an `<audio>` element cannot do this); `session.ts` wires capture → socket → playback. Capture is an **AudioWorklet streaming raw PCM16**, not MediaRecorder: no container means no ffmpeg on the server's realtime path and lower latency. The mic button (`components/workspace/voice-button.tsx`) is **always rendered**, including when `/api/voice/status` says voice is off — an earlier version returned `null` there, which made the whole feature undiscoverable, since nobody looks for a capability that leaves no trace in the UI. When it isn't ready the pill still shows and clicking it explains how to switch voice on.
  - `capture.ts` owns the AudioWorklet, the 16 kHz/20 ms frame geometry, and `pcm16ToWav`. It is shared by `session.ts` and the settings panel's microphone test **on purpose**: a test with its own copy of the capture path would pass happily while the real conversation was broken.
  - `config.ts` + `components/workspace/settings/voice-settings-page.tsx` are the settings panel. The test controls are the point — voice depends on server-side weights, an optional GPU and microphone permission, so a setting can be perfectly valid and still not work. The panel reports **measured** latency and RTF (an RTF above 1 means synthesis is slower than playback, which sounds like stuttering rather than an obvious error) and shows `device_requested` vs `device_actual`, so a `cuda` request that silently fell back to CPU is visible rather than mysterious.
  - `greeting.ts` is Nova's spoken hello on login, wired into `auth-success-toast.tsx`. It uses the **one-shot** `POST /api/voice/speak` rather than the duplex socket, so greeting you never prompts for the microphone — asking for the mic before the user has asked for anything is the fastest way to get it denied permanently. `greetingFor()` / `displayNameFrom()` are pure and unit-tested; `speak()` owns the I/O and reports `"blocked"` (not an error) when the browser refuses autoplay, which it normally does on a fresh navigation — the toast then offers a click-to-hear action.
  - Three deployment traps live in nginx, not here, and none can fail on `make dev`: `Permissions-Policy: microphone=()` disables `getUserMedia` app-wide (now `(self)`); a WebSocket path needs its own nginx `location` or the handshake 400s; and CSP has **no `media-src` fallback chain**, so without an explicit `media-src blob:` every spoken reply is silently refused in deployments only. All three pinned by `backend/tests/test_nginx_preview_headers.py`.
  - E2E uses Chromium's fake media device (`--use-fake-device-for-media-stream`) with an in-page WebSocket stub, so the real capture path runs with no backend. See `tests/e2e/voice.spec.ts`.
- **`hooks/`** — Shared React hooks
- **`lib/`** — Utilities (`cn()` from clsx + tailwind-merge)
- **`server/`** — Server-side code (better-auth, not yet active)
- **`styles/`** — Global CSS with Tailwind v4 `@import` syntax and CSS variables for theming

### Data Flow

1. User input → thread hooks (`core/threads/hooks.ts`) → LangGraph SDK streaming
2. Stream events update thread state (messages, artifacts, todos)
3. TanStack Query manages server state; localStorage stores user settings
4. Components subscribe to thread state and render updates

### Key Patterns

- **Server Components by default**, `"use client"` only for interactive components
- **Thread hooks** (`useThreadStream`, `useSubmitThread`, `useThreads`) are the primary API interface
- **LangGraph client** is a singleton obtained via `getAPIClient()` in `core/api/`
- **Environment validation** uses `@t3-oss/env-nextjs` with Zod schemas (`src/env.js`). Skip with `SKIP_ENV_VALIDATION=1`

## Code Style

- **Imports**: Enforced ordering (builtin → external → internal → parent → sibling), alphabetized, newlines between groups. Use inline type imports: `import { type Foo }`.
- **Unused variables**: Prefix with `_`.
- **Class names**: Use `cn()` from `@/lib/utils` for conditional Tailwind classes.
- **Path alias**: `@/*` maps to `src/*`.
- **Components**: `ui/` and `ai-elements/` are generated from registries (Shadcn, MagicUI, React Bits, Vercel AI SDK) — don't manually edit these.

## Environment

Backend API URLs are optional; an nginx proxy is used by default:

```
NEXT_PUBLIC_BACKEND_BASE_URL=http://localhost:8001
NEXT_PUBLIC_LANGGRAPH_BASE_URL=http://localhost:8001/api
```

Leave these unset for the standard `make dev` / Docker flow, where nginx serves
the public `/api/langgraph/*` prefix and rewrites it to Gateway's native `/api/*`
routes.

Requires Node.js 22+ and pnpm 10.26.2+.
