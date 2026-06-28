# DeerFlow 2.0 Frontend UI Audit

> **Scope.** Read-only audit of `/home/jahanzaib/Desktop/deer-flow/frontend`.
> Stack: Next.js 16 (App Router) + React 19 + Tailwind v4 + shadcn/ui +
> LangGraph SDK (`@langchain/langgraph-sdk`) + TanStack Query.
> **No files were modified.**

---

## 1. Component Tree (parent → children)

### 1.1 App shell

```
src/app/layout.tsx                       (RootLayout — html/body, fonts, Geist, JetBrains Mono)
└── <ThemeProvider>                      (next-themes; src/components/theme-provider.tsx)
    └── <QueryClientProvider>            (src/components/query-client-provider.tsx)
        └── <I18nProvider>               (src/core/i18n/context.tsx)
            └── children                 (each route)
```

`src/env.js` (T3 env validation) exposes `NEXT_PUBLIC_BACKEND_BASE_URL`,
`NEXT_PUBLIC_LANGGRAPH_BASE_URL`, `NEXT_PUBLIC_STATIC_WEBSITE_ONLY`,
`GITHUB_OAUTH_TOKEN` (server), etc.

### 1.2 Routes

```
src/app/
├── layout.tsx                                  ← RootLayout (Theme + Query + I18n)
├── page.tsx                                    (marketing/landing page; wraps <Landing/>)
├── blog/page.tsx                               (static blog)
├── (auth)/
│   ├── login/page.tsx                          (form + setup-status probe)
│   └── setup/page.tsx                          (admin first-run)
├── api/
│   └── memory/
│       ├── route.ts                            (proxy GET, DELETE)
│       └── [...path]/route.ts                  (proxy GET, POST, PATCH, DELETE)
├── mock/api/threads/[thread_id]/
│   ├── history/route.ts                        (static demo thread.json)
│   └── artifacts/[[...artifact_path]]/route.ts (serves /public/demo/threads/<id>/)
└── workspace/
    ├── layout.tsx                              (server: getServerSideUser → AuthProvider,
    │                                            GatewayOfflineFallback, QueryClient)
    ├── page.tsx                                (redirect → /workspace/chats/new)
    ├── workspace-content.tsx                   (SidebarProvider + SidebarInset + routes)
    ├── chats/
    │   ├── page.tsx                            (empty list view)
    │   ├── new/page.tsx                        (mounted under chats/[thread_id] layout)
    │   └── [thread_id]/
    │       ├── layout.tsx                      (server: pre-warm query cache + providers tree)
    │       ├── providers.tsx                   (SubtasksProvider > ArtifactsProvider > PromptInputProvider)
    │       └── page.tsx                        (main chat surface; mount layout = ChatBox > main)
    └── agents/
        ├── page.tsx                            (→ <AgentGallery/>)
        ├── new/page.tsx                        (two-step: name → chat; runs useThreadStream)
        └── [agent_name]/chats/[thread_id]/
            ├── layout.tsx                      (Providers tree, same as /chats/[thread_id]/layout)
            └── page.tsx                        (per-agent variant of the chat page)
```

### 1.3 `src/components/` directory

```
src/components/
├── query-client-provider.tsx            (TanStack QueryClient)
├── theme-provider.tsx                   (next-themes)
├── theme-toggle.tsx                     (light/dark switch)
├── ai-elements/                         (assistant-ui-styled primitives: artifact, conversation,
│                                         message, prompt-input, queue, reasoning, streamdown, …)
│   ├── artifact.tsx
│   ├── conversation.tsx                 (StickToBottom wrapper; use-stick-to-bottom)
│   ├── message.tsx                      (MessageResponse, MessageContent, MessageActions, …)
│   ├── prompt-input.tsx                 (model-selector, file-input, MCP selector, submit,
│   │                                     context provider — used by InputBox and NewAgentPage)
│   ├── queue.tsx                        (QueueList / QueueItem for the TodoList)
│   ├── reasoning.tsx                    (collapsible thinking)
│   └── streamdown.tsx                   (markdown renderer; rehype split-words plugin)
├── ui/                                  (shadcn primitives: button, card, dialog, sidebar,
│                                         resizable, tooltip, command, dropdown-menu, …)
│   ├── resizable.tsx                    (wraps react-resizable-panels; ResizablePanelGroup/Panel/Handle)
│   ├── sidebar.tsx                      (shadcn sidebar primitive; widths 16rem / 3rem; cookie-persisted)
│   ├── sheet.tsx                        (mobile-drawer shadcn primitive used by Sidebar)
│   └── …                                (~30 more: command, dropdown-menu, input, select, etc.)
├── landing/                             (homepage hero, header, footer)
└── workspace/                           (the real work surface)
    ├── workspace-container.tsx          (Container / Header / Body helper components)
    ├── workspace-sidebar.tsx            (Sidebar (shadcn) wrapping the four groups below)
    ├── workspace-header.tsx             (logo + "new chat" link + sidebar trigger)
    ├── workspace-nav-chat-list.tsx      (SidebarGroup: Chats / Agents links)
    ├── workspace-nav-menu.tsx           (footer: settings + github + report-issue)
    ├── recent-chat-list.tsx             (infinite-scroll list; rename/share/export/delete per row)
    ├── chat-box.tsx                     (top-level 2-panel ResizablePanelGroup:
    │                                     "chat" + "artifacts"; see §2)
    ├── input-box.tsx                    (prompt input + follow-up suggestions fetch + model picker)
    ├── thread-title.tsx                 (FlipDisplay for the doc title bar)
    ├── todo-list.tsx                    (collapsible QueueList of AgentThreadState.todos)
    ├── token-usage-indicator.tsx        (header dropdown with preset selector)
    ├── export-trigger.tsx               (Markdown / JSON export dropdown; client-side)
    ├── command-palette.tsx              (⌘K palette + shortcuts dialog)
    ├── gateway-offline-banner.tsx       (top banner; polls /api/v1/auth/me every ~10 s)
    ├── agents/
    │   ├── agent-gallery.tsx            (responsive grid of <AgentCard/>s)
    │   └── agent-card.tsx               (click → /workspace/agents/<name>/chats/new)
    ├── artifacts/
    │   ├── context.tsx                  (ArtifactsProvider — artifacts, selection, open/close)
    │   ├── artifact-trigger.tsx         (header button → setArtifactsOpen(true))
    │   ├── artifact-file-list.tsx       (Card list with download / install-skill actions)
    │   └── artifact-file-detail.tsx     (Artifact with header tabs: code | preview, iframe fallback)
    ├── channels/
    │   ├── workspace-channels-list.tsx   (SidebarGroup: connect/disconnect buttons)
    │   ├── channel-provider-icon.tsx
    │   ├── channel-runtime-config-dialog.tsx
    │   └── …
    ├── chats/
    │   ├── chat-box.tsx                 (the 2-panel container — see §2)
    │   ├── use-thread-chat.ts           (URL-derived thread-id state, mock flag, reset event)
    │   ├── use-chat-mode.ts
    │   └── index.ts
    ├── messages/
    │   ├── context.ts                   (ThreadContext: BaseStream<AgentThreadState> + isMock)
    │   ├── message-list.tsx             (StickToBottom; group-render logic)
    │   ├── message-list-item.tsx        (MessageResponse + actions; feedback submit)
    │   ├── message-group.tsx            (assistant / human / tool group container)
    │   ├── message-token-usage.tsx
    │   ├── skeleton.tsx                 (loading shimmer)
    │   ├── subtask-card.tsx             (renders one Subtask from SubtaskContext)
    │   ├── markdown-content.tsx         (Streamdown + ArtifactLink rewrite)
    │   └── …
    ├── citations/artifact-link.tsx      (rewrites /mnt/... hrefs → urlOfArtifact)
    ├── settings/                        (modal-based settings dialog with 6 sections)
    ├── landing/, …
    ├── tooltip.tsx, copy-button.tsx, streaming-indicator.tsx, github-icon.tsx, flip-display.tsx
    └── …
```

### 1.4 Provider hierarchy on a `/workspace/...` page (after auth)

```
RootLayout
└── ThemeProvider
    └── QueryClientProvider
        └── I18nProvider
            └── workspace/layout.tsx
                └── AuthProvider                           (initialUser from SSR)
                    └── WorkspaceContainer                 (sidebar shell)
                        ├── SidebarProvider                (shadcn; cookie-persisted)
                        │   ├── WorkspaceSidebar           (left rail)
                        │   │   ├── SidebarHeader          → WorkspaceHeader
                        │   │   ├── SidebarContent
                        │   │   │   ├── WorkspaceNavChatList
                        │   │   │   ├── WorkspaceChannelsList
                        │   │   │   └── RecentChatList
                        │   │   └── SidebarFooter          → WorkspaceNavMenu
                        │   └── SidebarInset                (main area)
                        │       └── WorkspaceHeader         (breadcrumb + github)
                        │           └── WorkspaceBody
                        │               └── [route]
                        └── GatewayOfflineFallback
                            └── chats/[thread_id]/layout.tsx (or agents/[agent_name]/chats/.../layout.tsx)
                                └── providers.tsx:
                                    SubtasksProvider
                                    └── ArtifactsProvider
                                        └── PromptInputProvider
                                            └── page.tsx
                                                └── ThreadContext.Provider          (page.tsx)
                                                    └── ChatBox                    (chat-box.tsx)
                                                        ├── ResizablePanel "chat"   (children)
                                                        │   └── <header> + <main>  (defined in page.tsx)
                                                        │       ├── MessageList
                                                        │       ├── (TodoList)
                                                        │       └── InputBox
                                                        └── ResizablePanel "artifacts" (chat-box.tsx)
                                                            ├── ArtifactFileDetail (when selected)
                                                            └── ArtifactFileList   (when not)
```

---

## 2. Layout structure and CSS grid

### 2.1 Top-level page chrome (`workspace-container.tsx`)

```
WorkspaceContainer                div  flex h-screen w-full flex-col
├── SidebarProvider wrapper       div  group/sidebar-wrapper has-data-[variant=inset]:bg-sidebar
│                                  flex min-h-svh w-full
│   ├── WorkspaceSidebar          <Sidebar variant="sidebar" collapsible="icon">
│   │   width: --sidebar-width = 16rem  | collapsed: --sidebar-width-icon = 3rem
│   │   ├── SidebarHeader → WorkspaceHeader (logo + new-chat link)
│   │   ├── SidebarContent → WorkspaceNavChatList + WorkspaceChannelsList + RecentChatList
│   │   ├── SidebarFooter → WorkspaceNavMenu
│   │   └── SidebarRail (drag-handle rail)
│   └── SidebarInset              (flex-1, ml=sidebar-width, transitions on collapse)
│       └── WorkspaceHeader       h-16, group-has-data-[collapsible=icon]/sidebar-wrapper:h-12
│           ├── Breadcrumb
│           └── github icon
│       └── WorkspaceBody
│           main > div flex h-full w-full flex-col items-center
│               └── [route content]
```

### 2.2 The actual chat "canvas" — `src/app/workspace/chats/[thread_id]/page.tsx`

The chat page is a **two-column flex inside `<ChatBox>`**:

```jsx
<div className="relative flex size-full min-h-0 justify-between">
  <header className="absolute top-0 right-0 left-0 z-30 flex h-12 …">
    <SidebarTrigger className="md:hidden" />
    … agent badge (only on /agents/[agent_name]/…) …
    <ThreadTitle />
    <div className="flex shrink-0 items-center sm:mr-4">
      <NewChatButton />
      <TokenUsageIndicator />
      <ExportTrigger />
      <ArtifactTrigger />
    </div>
  </header>

  <main className="flex min-h-0 max-w-full grow flex-col">
    <div className="flex min-h-0 flex-1 justify-center">
      <MessageList className="size-full …" />
    </div>
    <div className="right-0 bottom-0 left-0 z-30 flex justify-center px-3 sm:px-4">
      <div className="relative w-full max-w-(--container-width-md)">
        {" "}
        // = 204 * 0.25rem = 51rem ≈ 816px
        <TodoList />
        <InputBox />
      </div>
    </div>
  </main>
</div>
```

Layout summary:

| Region                | Class                                              | Notes                                                    |
| --------------------- | -------------------------------------------------- | -------------------------------------------------------- |
| Outer wrap            | `flex size-full min-h-0 justify-between`           | `size-full` = `h-full w-full`                            |
| Header                | `absolute top-0 left-0 right-0 z-30 h-12`          | Floats above; backdrop-blur in normal mode               |
| Main                  | `flex min-h-0 max-w-full grow flex-col`            | Single column; only `MessageList` + Input dock           |
| MessageList container | `flex min-h-0 flex-1 justify-center`               | The list is `size-full`                                  |
| Input dock            | `flex justify-center px-3 sm:px-4`                 | `<div>` inside has `w-full max-w-(--container-width-md)` |
| Welcome-mode dock     | `absolute` (vertically centered with `-translate`) | Same inner `max-w-(--container-width-sm)`                |

### 2.3 The only real panel split — `chat-box.tsx`

There is exactly **one** resizable surface in the whole UI, and it has only
**two** panels:

```tsx
<ResizablePanelGroup
  id={…}
  orientation="horizontal"
  defaultLayout={{ chat: 100, artifacts: 0 }}
  groupRef={layoutRef}
>
  <ResizablePanel className="relative" defaultSize={100} id="chat">   {/* ← chat surface */}
    {children}
  </ResizablePanel>
  <ResizableHandle … />
  <ResizablePanel
    className={cn("transition-all …", !artifactsOpen && "opacity-0")}
    id="artifacts"                                                    {/* ← artifact surface */}
  >
    {/* ArtifactFileDetail or ArtifactFileList */}
  </ResizablePanel>
</ResizablePanelGroup>
```

Two presets:

```ts
const CLOSE_MODE = { chat: 100, artifacts: 0 };
const OPEN_MODE = { chat: 60, artifacts: 40 };
```

Layout is driven imperatively via `layoutRef.current.setLayout(OPEN_MODE | CLOSE_MODE)`
when `artifactPanelOpen` flips (toggle from `ArtifactTrigger` or auto-open on
first artifact).

### 2.4 shadcn `Sidebar` (the left rail)

CSS variables (`src/styles/globals.css`, line 387):

```css
:root {
  --container-width-xs: calc(var(--spacing) * 72); /* 18rem */
  --container-width-sm: calc(var(--spacing) * 144); /* 36rem */
  --container-width-md: calc(var(--spacing) * 204); /* 51rem */
  --container-width-lg: calc(var(--spacing) * 256); /* 64rem */
}
```

Sidebar variables (in `ui/sidebar.tsx`):

```ts
SIDEBAR_WIDTH = "16rem";
SIDEBAR_WIDTH_MOBILE = "18rem";
SIDEBAR_WIDTH_ICON = "3rem";
SIDEBAR_KEYBOARD_SHORTCUT = "b"; // ⌘B / Ctrl+B
```

`Sidebar` renders as a fixed `<aside>` (md+) or `<Sheet>` (mobile). Width
collapse uses `transition-[width]` and is cookie-persisted (`sidebar_state`,
7-day TTL).

### 2.5 Resizable primitive

`src/components/ui/resizable.tsx` wraps `react-resizable-panels`:

- `flex h-full w-full` (horizontal) or `flex-col` (vertical)
- Handle: `w-px bg-border`, hover: `opacity-100`, focus ring, optional grip icon
- Imperative ref via `GroupImperativeHandle` (`setLayout({...})`)

### 2.6 Other grid/responsive surfaces

| File                                                           | Layout                                                          |
| -------------------------------------------------------------- | --------------------------------------------------------------- |
| `components/workspace/agents/agent-gallery.tsx`                | `grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4` |
| `components/workspace/agents/agent-gallery.tsx` (`AgentCard`)  | card grid; individual card uses `flex`                          |
| `components/workspace/recent-chat-list.tsx`                    | Vertical `flex flex-col gap-1`                                  |
| `components/workspace/channels/workspace-channels-list.tsx`    | `SidebarGroup` of providers (vertical)                          |
| `components/workspace/token-usage-indicator.tsx` (preset list) | `grid gap-0.5` inside dropdown items                            |
| `components/ai-elements/conversation.tsx`                      | `StickToBottom` (`use-stick-to-bottom`); flex column            |
| `components/workspace/input-box.tsx`                           | `flex` columns, with floating follow-up chips                   |

No CSS grid layout exists outside of `agent-gallery.tsx`. Everything else is
`flex` + width/height utilities.

---

## 3. WebSocket / SSE connections to backend

### 3.1 Direct EventSource / WebSocket

`grep -rn 'EventSource\|WebSocket\|text/event-stream\|new WebSocket' frontend/src` → **0 matches**.

There are **no direct** `EventSource`, `WebSocket`, or `text/event-stream`
consumers in the codebase. All real-time streaming goes through the
LangGraph SDK.

### 3.2 LangGraph SDK streaming

The single real-time source is `useStream` from `@langchain/langgraph-sdk/react`,
configured once in `src/core/threads/hooks.ts`:

```ts
import { useStream } from "@langchain/langgraph-sdk/react";
…
const thread = useStream<AgentThreadState>({
  client: getAPIClient(isMock),     // src/core/api/api-client.ts
  assistantId: "lead_agent",
  threadId: onStreamThreadId,
  reconnectOnMount: true,
  fetchStateHistory: { limit: 1 },
  onCreated(meta) { … },            // upserts thread in query cache + posts agent_name metadata
  onLangChainEvent(event) { … },    // forwards on_tool_end → onToolEnd listener
  onUpdateEvent(data) { … },        // summarisation middleware → recompute pendingUsageMessages
  onCustomEvent(event) { … },       // subtask tool-call bridge into SubtasksContext
  onError(error) { … },
  onFinish(state) { listeners.current.onFinish?.(state.values); },
});
```

The SDK opens its own stream to the configured base URL:

```ts
// src/core/config/index.ts
export function getLangGraphBaseURL(isMock?: boolean) {
  if (env.NEXT_PUBLIC_LANGGRAPH_BASE_URL) …
  else if (isMock) return `${window.location.origin}/mock/api`;
  else return `${window.location.origin}/api/langgraph`;        // ← primary
}
```

`Client` is constructed in `src/core/api/api-client.ts`. It wraps
`@langchain/langgraph-sdk/client.Client` and:

- sets an `onRequest` hook that injects `X-CSRF-Token` on state-changing calls
  (see §4.2),
- patches `client.runs.stream` and `client.runs.joinStream` to disable any
  per-call CSRF strip and to recover from "inactive run stream" (`HTTP 409 … not
active on this worker … cannot be streamed`),
- short-circuits `runs.list`, `runs.stream`, `runs.joinStream` to empty
  generators when `isStaticWebsiteOnly()` (the public demo).

### 3.3 Pollers / intervals (also "long-lived")

| Source                                                                        | Trigger                                     | Cadence                                                                   |
| ----------------------------------------------------------------------------- | ------------------------------------------- | ------------------------------------------------------------------------- |
| `components/workspace/gateway-offline-banner.tsx`                             | `gatewayUnavailable` prop + `user === null` | every 10 s (`OFFLINE_BANNER_RETRY_INTERVAL_MS`)                           |
| `core/auth/AuthProvider.tsx`                                                  | `visibilitychange` → `visible`              | throttled to once per 60 s                                                |
| `core/channels/connect-poll.ts`                                               | `useConnectChannelProvider` `onSuccess`     | every 2 s (`CONNECT_POLL_INTERVAL_MS`) up to `expires_in` (default 600 s) |
| `components/workspace/messages/message-list.tsx` (`LoadMoreHistoryIndicator`) | top sentinel IntersectionObserver           | 1.2 s throttle (`LOAD_MORE_HISTORY_THROTTLE_MS`)                          |
| `components/workspace/recent-chat-list.tsx`                                   | sentinel IntersectionObserver               | `rootMargin: "120px 0px 120px 0px"`                                       |
| `components/workspace/messages/context.ts` + thread hooks                     | LangGraph SDK reconnects on mount           | "reconnectOnMount: true"                                                  |

### 3.4 PostMessage iframe channel

`ArtifactFilePreview` listens on `window.message` from the sandboxed HTML
preview iframe to round-trip scroll position:

```
source: HTML_PREVIEW_SCROLL_MESSAGE_SOURCE
key:    createHtmlPreviewScrollKey(scrollKey)
types:  "save" | "restore-request"
```

### 3.5 `dynamic = 'force-dynamic'` route

`src/app/api/memory/[...path]/route.ts` and `route.ts` use
`NextRequest` → `fetch(process.env.NEXT_PUBLIC_BACKEND_BASE_URL)` (see §4.1).

---

## 4. API calls to FastAPI gateway and LangGraph

### 4.1 Transport conventions

- `getBackendBaseURL()` (`src/core/config/index.ts`) → `env.NEXT_PUBLIC_BACKEND_BASE_URL`
  (defaults to empty string; relative to `window.location.origin`).
- `getLangGraphBaseURL()` → `env.NEXT_PUBLIC_LANGGRAPH_BASE_URL`, or
  `<origin>/api/langgraph` (default) / `<origin>/mock/api` (when `?mock=true`).
- Wrapper `fetch()` in `src/core/api/fetcher.ts` adds:
  - `credentials: "include"` (access_token cookie is HttpOnly).
  - `X-CSRF-Token: <csrf_token cookie>` for `POST|PUT|DELETE|PATCH`.
  - Auto-redirect to `/login?...` on `401`.
- LangGraph SDK uses an `onRequest` hook in `api-client.ts` to mirror the CSRF
  contract on `client.runs.*`.
- `src/app/api/memory/**` is a server-side proxy to
  `process.env.NEXT_PUBLIC_BACKEND_BASE_URL` (default `http://127.0.0.1:8001`).

### 4.2 FastAPI gateway endpoints (and where they are called)

| Method | Path (relative to `getBackendBaseURL()`)        | Caller(s)                                                                             |
| ------ | ----------------------------------------------- | ------------------------------------------------------------------------------------- |
| GET    | `/api/models`                                   | `core/models/api.ts` → `useModels` (`MessageList`-adjacent models)                    |
| GET    | `/api/agents`                                   | `core/agents/api.ts` → `useAgents` (AgentGallery)                                     |
| POST   | `/api/agents`                                   | `core/agents/api.ts` → `useCreateAgent` (admin form)                                  |
| GET    | `/api/agents/{name}`                            | `core/agents/api.ts` → `useAgent`, also `getAgentWithRetry` in `agents/new/page.tsx`  |
| PUT    | `/api/agents/{name}`                            | `core/agents/api.ts` → `useUpdateAgent`                                               |
| DELETE | `/api/agents/{name}`                            | `core/agents/api.ts` → `useDeleteAgent`                                               |
| GET    | `/api/agents/check?name=...`                    | `core/agents/api.ts` → `checkAgentName` (`agents/new`)                                |
| GET    | `/api/skills`                                   | `core/skills/api.ts` → `loadSkills`                                                   |
| POST   | `/api/skills/{name}`                            | `core/skills/api.ts` → `enableSkill`                                                  |
| POST   | `/api/skills/install`                           | `core/skills/api.ts` → `installSkill` (artifact list/detail)                          |
| GET    | `/api/memory`                                   | `core/memory/api.ts` → `loadMemory`; also proxied via `src/app/api/memory/route.ts`   |
| DELETE | `/api/memory`                                   | `core/memory/api.ts` → `clearMemory`; proxied                                         |
| GET    | `/api/memory/facts/{factId}`                    | `core/memory/api.ts` → `useMemoryFact` (proxy too)                                    |
| POST   | `/api/memory/facts`                             | `core/memory/api.ts` → `createMemoryFact`                                             |
| PATCH  | `/api/memory/facts/{factId}`                    | `core/memory/api.ts` → `updateMemoryFact`                                             |
| DELETE | `/api/memory/facts/{factId}`                    | `core/memory/api.ts` → `deleteMemoryFact`                                             |
| GET    | `/api/memory/export`                            | `core/memory/api.ts` → `exportMemory` (browser download)                              |
| POST   | `/api/memory/import`                            | `core/memory/api.ts` → `importMemory` (multipart)                                     |
| GET    | `/api/mcp/config`                               | `core/mcp/api.ts` → `loadMCPConfig`                                                   |
| PUT    | `/api/mcp/config`                               | `core/mcp/api.ts` → `saveMCPConfig`                                                   |
| GET    | `/api/channels/providers`                       | `core/channels/api.ts` → `listChannelProviders`                                       |
| GET    | `/api/channels/connections`                     | `core/channels/api.ts` → `listChannelConnections`                                     |
| POST   | `/api/channels/{provider}/connect`              | `core/channels/api.ts` → `connectChannelProvider`                                     |
| PUT    | ` /api/channels/{provider}/config`              | `core/channels/api.ts` → `configureChannelProvider`                                   |
| POST   | `/api/channels/{provider}/disconnect`           | `core/channels/api.ts` → `disconnectChannelProvider`                                  |
| DELETE | `/api/channels/connections/{connectionId}`      | `core/channels/api.ts` → `disconnectChannelConnection`                                |
| GET    | `/api/suggestions/config`                       | `core/suggestions/api.ts` → `loadSuggestionsConfig` (404 → enabled)                   |
| POST   | `/api/threads/{threadId}/suggestions`           | `components/workspace/input-box.tsx` (raw `fetch`, not the wrapper) — follow-up chips |
| GET    | `/api/threads/{threadId}/token-usage`           | `core/threads/api.ts` → `useThreadTokenUsage`                                         |
| POST   | `/api/threads/{threadId}/runs/{runId}/feedback` | `core/api/feedback.ts` (thumbs up/down on AI messages)                                |

Same-origin auth routes (Next.js → FastAPI, hits gateway via Vite/Next rewrites):

| Method | Path                           | Caller                                                                                                  |
| ------ | ------------------------------ | ------------------------------------------------------------------------------------------------------- |
| GET    | `/api/v1/auth/me`              | `core/auth/AuthProvider.tsx` (`refreshUser`), `gateway-offline-banner.tsx`, `core/auth/server.ts` (SSR) |
| POST   | `/api/v1/auth/logout`          | `core/auth/AuthProvider.tsx` (`logout`)                                                                 |
| GET    | `/api/v1/auth/setup-status`    | `app/(auth)/login/page.tsx`, `app/(auth)/setup/page.tsx`                                                |
| POST   | `/api/v1/auth/login/local`     | `app/(auth)/login/page.tsx`                                                                             |
| POST   | `/api/v1/auth/register`        | `app/(auth)/login/page.tsx`                                                                             |
| POST   | `/api/v1/auth/initialize`      | `app/(auth)/setup/page.tsx`                                                                             |
| POST   | `/api/v1/auth/change-password` | `app/(auth)/setup/page.tsx`, `components/workspace/settings/account-settings-page.tsx`                  |

Static / mock (used only when `NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"`):

| URL                                                                     | Used by                                                           |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `GET /demo/threads/{threadId}/thread.json`                              | `core/threads/static-demo.ts` (loaded via `globalThis.fetch`)     |
| `GET /mock/api/threads/{threadId}/history`                              | Next route handler reads `public/demo/threads/<id>/thread.json`   |
| `GET /mock/api/threads/{threadId}/artifacts/...` (and `?download=true`) | Next route handler serves files under `public/demo/threads/<id>/` |
| `GET /api/threads/{threadId}/artifacts/...` (`isMock=true`)             | `core/artifacts/utils.ts` switches backend↔mock via `isMock` flag |

> **Note.** `/api/langgraph/*` is the **rewritten** LangGraph base used by the
> SDK — handled by Next.js rewrites (or your reverse proxy in prod) and not
> enumerated here because all SDK calls happen inside `useStream`.

### 4.3 LangGraph SDK endpoints (used by `useStream` + `api-client.ts`)

The full `ThreadsClient` / `RunsClient` / `AssistantsClient` surface from
`@langchain/langgraph-sdk` is exposed via `getAPIClient()`. Call sites in this
codebase:

| SDK method                                            | Where                                                                                                                                |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `client.runs.stream(threadId, "lead_agent", payload)` | `useStream({ assistantId: "lead_agent", … })` (transitively all message sending)                                                     |
| `client.runs.joinStream(threadId, runId)`             | Reconnect logic in `api-client.ts` patch                                                                                             |
| `client.threads.getState<AgentThreadState>(threadId)` | `components/workspace/recent-chat-list.tsx` (`handleExport`)                                                                         |
| `client.threads.update(threadId, { metadata })`       | `useStream.onCreated` (writes `agent_name`)                                                                                          |
| `client.threads.search(params, …)`                    | `useInfiniteThreads` / `useThreads` (`core/threads/hooks.ts`) — uses `useInfiniteQuery` with `pageParam` derived from `pages.flat()` |
| `client.threads.create({ … })`                        | Implicit via `client.runs.stream` with a new threadId (no `client.threads.create` direct call here; threads are created by the run)  |
| `client.threads.delete(threadId)`                     | `useDeleteThread` (via `core/threads/hooks.ts`)                                                                                      |

SDK state history (`fetchStateHistory: { limit: 1 }`) is used by `useStream`
to bootstrap messages before joining the live stream.

### 4.4 Things that intentionally bypass the wrapper

- `core/auth/server.ts` — SSR fetch (cookies from `next/headers`).
- `core/api/api-client.ts` — uses `globalThis.fetch` so the SDK can patch the
  `onRequest` interceptor (CSRF) but **doesn't** trigger the wrapper's
  401-redirect.
- `core/threads/static-demo.ts` — `globalThis.fetch` for `/demo/...` static assets.
- `components/workspace/input-box.tsx` (suggestions) — uses raw `fetch(...)`
  without CSRF or auto-redirect, but the call is `POST` so this _would_ 403
  without a CSRF token in strict mode. (Today it works because
  `/api/threads/{threadId}/suggestions` is likely a state-changing endpoint
  exempt from CSRF in the gateway, or because the wrapper omission is a bug.
  Flagging for a 4-panel work item.)
- `core/artifacts/loader.ts` — raw `fetch(absoluteUrl)` (cross-origin, no
  cookies needed).

---

## 5. Files that would need changing for a **4-panel layout**

A 4-panel resizable grid (e.g. **Sidebar (left)** + **Chat (centre)** +
**Artifacts (right)** + **Agent tools / todos (bottom)**) means turning the
current 2-panel `ResizablePanelGroup` into a 4-panel one, plus coordinating
the third region. Below is the exhaustive list of files to touch, grouped by
the nature of the change.

### 5.1 Core layout containers (must change)

| File                                                                 | Change                                                                                                                                                                                                                                                                |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/components/workspace/chats/chat-box.tsx`                        | Replace 2-panel `ResizablePanelGroup` with a 4-panel grid (or nested groups: vertical split, then horizontal). Add the new `OPEN_MODE`/`CLOSE_MODE` presets and the `setLayout` calls for each panel. Update the imperative ref type if a nested group is introduced. |
| `src/app/workspace/chats/[thread_id]/providers.tsx`                  | Possibly introduce a fourth context (e.g. `AgentToolsProvider`) if the new panel owns state.                                                                                                                                                                          |
| `src/app/workspace/agents/[agent_name]/chats/[thread_id]/layout.tsx` | Mirror the providers change above.                                                                                                                                                                                                                                    |
| `src/app/workspace/chats/[thread_id]/page.tsx`                       | Wire the new header trigger(s) (open/close the 3rd panel), pass `selectedTool` state into ChatBox.                                                                                                                                                                    |
| `src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`   | Same as above for the per-agent variant.                                                                                                                                                                                                                              |
| `src/components/ui/resizable.tsx`                                    | If you need a _vertical_ outer group + horizontal inner group, no changes required (the primitive already supports both orientations); otherwise add helpers.                                                                                                         |
| `src/styles/globals.css`                                             | Add new `--container-width-*` tokens if the new panel needs a different max-width than the existing four (`xs`/`sm`/`md`/`lg`).                                                                                                                                       |
| `src/components/workspace/todo-list.tsx`                             | This currently renders as an **overlay card** over the InputBox. For a 4-panel grid, lift it into the bottom panel as the primary content; remove the absolute-positioning classes.                                                                                   |

### 5.2 New panel content (new components to create)

| Purpose                                                | Suggested new file                                                                                                     |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| Bottom panel shell                                     | `src/components/workspace/agent-tools/agent-tools-panel.tsx` (or similar)                                              |
| Subtask list rendered in bottom panel                  | Lift `subtask-card.tsx` and `SubtasksContext` usage out of `message-list.tsx` and into the new panel.                  |
| TodoList (already exists)                              | Move it from overlay → panel content; or keep both and decide per context.                                             |
| Per-thread tool-call registry / status / debug console | `src/components/workspace/agent-tools/agent-tools-tool-call-list.tsx`                                                  |
| Settings for the new panel (toggle bottom dock, etc.)  | Add a section in `src/components/workspace/settings/tools-settings-section.tsx` (and register in `settings/index.ts`). |

### 5.3 Components inside ChatBox that need their container queries updated

These all currently assume they're inside a flex column or a fixed-width
right-hand panel; once you add a bottom panel the heights must be `flex-1` /
`min-h-0` aware:

| File                                                          | Change                                                                                                                                                                                                                        |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/components/workspace/artifacts/context.tsx`              | Add `bottomPanelOpen` state (or rename to a `panels` reducer); wire to `ChatBox`.                                                                                                                                             |
| `src/components/workspace/artifacts/artifact-trigger.tsx`     | If you also add a header trigger for the new panel.                                                                                                                                                                           |
| `src/components/workspace/artifacts/artifact-file-list.tsx`   | Only CSS impact (height fill).                                                                                                                                                                                                |
| `src/components/workspace/artifacts/artifact-file-detail.tsx` | Only CSS impact; ensure `iframe` is `size-full` and parent is `min-h-0`.                                                                                                                                                      |
| `src/components/workspace/input-box.tsx`                      | If the InputBox becomes a sibling of the new panel rather than a footer dock, move the dock logic out of `page.tsx`.                                                                                                          |
| `src/components/workspace/messages/message-list.tsx`          | The `MESSAGE_LIST_DEFAULT_PADDING_BOTTOM` constant may need a new value if the bottom panel is collapsible; verify `Conversation` (`flex-1 overflow-y-hidden` from `ai-elements/conversation.tsx`) still gets correct height. |
| `src/components/workspace/todo-list.tsx`                      | Convert from absolute-positioned overlay to a panel-resident component (see §5.1).                                                                                                                                            |
| `src/components/workspace/export-trigger.tsx`                 | Header-only; no functional change.                                                                                                                                                                                            |
| `src/components/workspace/token-usage-indicator.tsx`          | Header-only; no functional change.                                                                                                                                                                                            |
| `src/components/workspace/thread-title.tsx`                   | Header-only; no functional change.                                                                                                                                                                                            |
| `src/components/workspace/gateway-offline-banner.tsx`         | Banner positioned `absolute` — should still work, but verify z-index above 4 panels.                                                                                                                                          |
| `src/components/workspace/command-palette.tsx`                | Add a command to toggle the new panel if you want.                                                                                                                                                                            |

### 5.4 Hooks and contexts (state plumbing)

| File                                                | Change                                                                                     |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `src/components/workspace/messages/context.ts`      | Extend `ThreadContextType` only if the new panel needs thread-scoped state.                |
| `src/components/workspace/artifacts/context.tsx`    | Lift panel-state into a more general `PanelsContext` (artifacts + new panel).              |
| `src/core/tasks/context.tsx`                        | Already thread-scoped. No change unless you want per-panel filtering.                      |
| `src/core/threads/hooks.ts`                         | The `runsRef` / optimistic-message plumbing does not need to change for the layout itself. |
| `src/components/workspace/chats/use-thread-chat.ts` | No change.                                                                                 |
| `src/hooks/use-global-shortcuts.ts`                 | Add a shortcut for toggling the new panel (optional, via `CommandPalette`).                |
| `src/components/workspace/command-palette.tsx`      | Register the new shortcut.                                                                 |

### 5.5 I18N strings to add

| File                             | Change                                                                  |
| -------------------------------- | ----------------------------------------------------------------------- |
| `src/core/i18n/locales/en-US.ts` | Add keys for the new panel title, toggle, and any new tool-call labels. |
| `src/core/i18n/locales/zh-CN.ts` | Mirror in zh-CN.                                                        |
| `src/core/i18n/locales/types.ts` | Update the dictionary types so the new keys are type-safe.              |

### 5.6 Settings dialog (if you want the new panel toggleable)

| File                                                           | Change                                                                                           |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `src/components/workspace/settings/index.ts`                   | Register the new section (e.g. `"agent-tools"`).                                                 |
| `src/components/workspace/settings/tools-settings-section.tsx` | New section file: toggle "Show agent tools panel", default size, collapsed/expanded start state. |
| `src/core/settings/local.ts`                                   | Persist the new toggles in `localStorage`.                                                       |
| `src/core/settings/store.ts`                                   | Thread-scoped persistence (if per-thread visibility is desired).                                 |
| `src/core/settings/index.ts`                                   | Add the new settings keys + hooks.                                                               |

### 5.7 Static / mock route surface (likely no change, but verify)

| File                                                                           | Change                            |
| ------------------------------------------------------------------------------ | --------------------------------- |
| `src/app/mock/api/threads/[thread_id]/history/route.ts`                        | None.                             |
| `src/app/mock/api/threads/[thread_id]/artifacts/[[...artifact_path]]/route.ts` | None.                             |
| `src/core/threads/static-demo.ts`                                              | None.                             |
| `src/core/artifacts/utils.ts`                                                  | None (URL builder is unaffected). |

### 5.8 Backend / API surface

**No backend changes are strictly required** to host a 4-panel layout, but if
the new panel needs data the current surface lacks:

| Possible addition                                          | Owner   | Notes                                                                        |
| ---------------------------------------------------------- | ------- | ---------------------------------------------------------------------------- |
| `GET /api/threads/{threadId}/subagents` (subtask registry) | Backend | If you want a long-lived "tools" list rather than per-message cards.         |
| `GET /api/threads/{threadId}/tool-calls`                   | Backend | Same.                                                                        |
| `GET /api/threads/{threadId}/todos`                        | Backend | Currently `useStream.onUpdateEvent` derives them; if persisted, expose them. |

### 5.9 Tests / docs (low priority for an audit, flagged for completeness)

| File                                       | Change                                                                                 |
| ------------------------------------------ | -------------------------------------------------------------------------------------- |
| `frontend/CLAUDE.md`, `frontend/AGENTS.md` | Document the 4-panel structure and the new providers tree.                             |
| Any Cypress / Playwright specs             | Update selectors that target `aria-label` / `[data-testid]` inside the affected areas. |

---

## Appendix A — Quick file index (full file list of `src/core/` and `src/components/workspace/`)

### `src/core/`

```
agents/{api,hooks,index,types}.ts
api/{api-client,feedback,fetcher,index,stream-mode}.ts
artifacts/{hooks,index,loader,preview,utils}.ts   ← NO api.ts; all reads go through loader + utils
auth/{AuthProvider.tsx,auth-disabled-user.ts,gateway-config.ts,proxy-policy.ts,server.ts,static-user.ts,types.ts}
blog/index.ts
channels/{api,connect-poll,hooks,open-connect-url,provider-state,types}.ts
clipboard.ts
config/index.ts
i18n/{context.tsx,cookies.ts,hooks.ts,index.ts,locale.ts,server.ts,translations.ts,locales/{en-US,zh-CN,index,types}.ts(x)}
mcp/{api,hooks,index,types}.ts
memory/{api,hooks,index,types}.ts
messages/{usage-model,usage,utils}.ts
models/{api,hooks,index,types}.ts
notification/hooks.ts
rehype/index.ts
settings/{hooks,index,local,store}.ts
skills/{api,hooks,index,type}.ts
static-mode.ts
streamdown/{index,mermaid,plugins,preprocess}.ts
suggestions/{api,hooks}.ts
tasks/{context.tsx,index,subtask-result,types}.ts
threads/{api,export,hooks,index,static-demo,thread-search-query,token-usage,types,utils}.ts
todos/{index,types}.ts
tools/utils.ts
uploads/{api,file-validation,hooks,index,prompt-input-files}.ts
utils/{datetime,files.tsx,json,markdown,uuid}.ts
```

### `src/components/workspace/`

```
agents/{agent-card,agent-gallery}.tsx
artifacts/{artifact-file-detail,artifact-file-list,artifact-trigger,context.tsx,index.ts}
channels/{workspace-channels-list,channel-provider-icon,channel-runtime-config-dialog,…}.tsx
chats/{chat-box,use-chat-mode,use-thread-chat,index}.ts(x)
citations/artifact-link.tsx
messages/{context,message-list,message-list-item,message-group,message-token-usage,skeleton,subtask-card,markdown-content,…}.ts(x)
settings/{account-settings-page,appearance-settings-section,…,index}.tsx
{agent-welcome,chat-box,code-editor,command-palette,copy-button,export-trigger,
 flip-display,gateway-offline-banner,gateway-offline-banner-helpers,github-icon,
 input-box,recent-chat-list,streaming-indicator,thread-channel-source,thread-title,
 todo-list,token-usage-indicator,tooltip,workspace-container,workspace-header,
 workspace-nav-chat-list,workspace-nav-menu,workspace-sidebar,…}.ts(x)
```

---

## Appendix B — Provider/state ownership map

| Concern                         | Provider                                                           | Hook / API                                            |
| ------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------- |
| Authentication                  | `AuthProvider` (`core/auth`)                                       | `useAuth`, `useRequireAuth`                           |
| QueryClient (REST cache)        | `QueryClientProvider` (top-level)                                  | `useQuery`, `useMutation`, `useInfiniteQuery`         |
| i18n                            | `I18nProvider` (`core/i18n`)                                       | `useI18n`                                             |
| Theme                           | `ThemeProvider` (next-themes)                                      | `useTheme`                                            |
| Sidebar (left rail)             | `SidebarProvider` (shadcn)                                         | `useSidebar`                                          |
| Thread (LangGraph `BaseStream`) | `ThreadContext` (`components/workspace/messages/context.ts`)       | `useThread`                                           |
| Subtasks                        | `SubtasksProvider` (`core/tasks/context.tsx`)                      | `useSubtask`, `useUpdateSubtask`, `useSubtaskContext` |
| Artifacts                       | `ArtifactsProvider` (`components/workspace/artifacts/context.tsx`) | `useArtifacts`                                        |
| Prompt input (model/MCP/files)  | `PromptInputProvider` (`components/ai-elements/prompt-input.tsx`)  | `usePromptInput`                                      |
| Thread id + mock flag           | `useThreadChat`                                                    | (URL-derived state, no context)                       |
| Panels (post-4-panel layout)    | **NEW** `PanelsProvider`                                           | `usePanels`                                           |
