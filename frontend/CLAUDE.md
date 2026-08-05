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
  - **Every tab stays mounted** and is only `hidden` via CSS (`agent-computer-panel.tsx`). So any tab that fetches, polls, streams or holds a socket **must** take an `active` prop and gate on it. Getting this wrong is invisible in the UI and expensive: a missed gate left an `EventSource` open for the life of the thread (`review-tab`), re-downloaded a whole deliverable every 2s (`browser-tab`), and held a live ttyd PTY and a noVNC video stream while off-screen. Note `refetchIntervalInBackground: false` does **not** help — it only covers a backgrounded browser *window*, not a CSS-hidden subtree.
  - **The file tree's namespace** (`files-tab.tsx::treePathOf`). Workspace files are flattened to the top level while `outputs/` and `uploads/` keep their mount folder, so the workspace's own `outputs/` directory would collide with the `/mnt/user-data/outputs` mount — one file silently overwrote the other while the header counted both. `treePathOf` disambiguates only the genuinely ambiguous paths. A node can also be a file *and* a folder at once (a file `build` beside `build/out.js`); both branches must render or the subtree vanishes. Covered by `tests/unit/components/workspace/files-tab-dedupe.test.ts`.
  - **Downloads** go through `urlOfArtifact({ download: true })` (`core/artifacts/utils.ts`) → the artifacts endpoint, which streams real bytes, enforces ownership, and sets an RFC 5987 `Content-Disposition`. Do **not** build downloads on `/api/sandbox/file`: it returns JSON and `read_text()`s the file, so it corrupts every binary.
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
