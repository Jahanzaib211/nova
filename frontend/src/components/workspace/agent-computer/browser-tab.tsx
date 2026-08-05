"use client";

import {
  AlertTriangleIcon,
  CodeIcon,
  DownloadIcon,
  ExternalLinkIcon,
  EyeIcon,
  GlobeIcon,
  LoaderCircleIcon,
  MonitorIcon,
  RefreshCwIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import {
  useBrowserCheck,
  useLastBrowserCheck,
  useSandboxFile,
  useSandboxTerminalUrl,
} from "@/core/sandbox/hooks";
import { cn } from "@/lib/utils";

// Tab 3: Browser — rendered HTML preview
// ──────────────────────────────────────────────────────────
export function Browser({
  threadId,
  filePath,
  devServer,
  devServers = [],
  selectedLabel = "app",
  onSelectLabel,
  onStartPreview,
  hasRunnableProject = false,
  onAgentMessage,
  entryHtmlArtifact = null,
  active = true,
}: {
  threadId: string;
  filePath: string | null;
  devServer: {
    running: boolean;
    status: string;
    port: number | null;
    url: string | null;
    compiles?: number;
  };
  devServers?: { label: string; running: boolean }[];
  selectedLabel?: string;
  onSelectLabel?: (label: string) => void;
  onStartPreview?: (label?: string) => void | Promise<void>;
  hasRunnableProject?: boolean;
  onAgentMessage?: (text: string) => void;
  entryHtmlArtifact?: string | null;
  /** Gates the self-test poll so it doesn't run while the tab is hidden but stays mounted. */
  active?: boolean;
}) {
  const { t } = useI18n();
  // Only poll the static-preview file when its result can actually be shown:
  // the tab is visible AND the live dev server isn't taking precedence (when it
  // is, the component returns early and `content` is never read). This is a 2s
  // poll of the file's ENTIRE contents, so leaving it on while hidden
  // re-downloaded the whole deliverable 30x/minute for the life of the thread.
  const staticPreviewNeeded = Boolean(active) && !devServer.running;
  const { content, exists, isLoading } = useSandboxFile(
    threadId,
    filePath,
    staticPreviewNeeded,
  );
  const [viewMode, setViewMode] = useState<"desktop" | "mobile">("desktop");
  const [reloadKey, setReloadKey] = useState(0);
  const filename = filePath?.split("/").at(-1) ?? "";
  const isHtml = filename.endsWith(".html") || filename.endsWith(".htm");

  // ── Browser chrome: address bar + back/forward over our own history stack ──
  // The proxied app runs at an opaque origin (no allow-same-origin), so we can't
  // observe in-app link clicks; the address bar drives navigation to a route and
  // back/forward replay the routes we pushed. Routes reset when the server/label
  // changes (prefix changes).
  const prefix = (devServer.url ?? "").replace(/\/$/, "");
  // For a single static HTML deliverable whose entry isn't index.html, default the
  // preview route to that file so the dev-server root ("/") doesn't 404 to a blank
  // white page. Framework dev servers ship no raw .html artifact → entryRoute "/".
  const entryRoute = useMemo(() => {
    const base = (entryHtmlArtifact ?? "").split("/").at(-1) ?? "";
    return /\.html?$/i.test(base) && base.toLowerCase() !== "index.html"
      ? `/${base}`
      : "/";
  }, [entryHtmlArtifact]);
  const [navStack, setNavStack] = useState<string[]>([entryRoute]);
  const [navIdx, setNavIdx] = useState(0);
  const route = navStack[navIdx] ?? "/";
  const [routeInput, setRouteInput] = useState(entryRoute);
  useEffect(() => setRouteInput(route), [route]);
  useEffect(() => {
    // New dev server / switched label / new deliverable → fresh history at entry route.
    setNavStack([entryRoute]);
    setNavIdx(0);
  }, [prefix, entryRoute]);
  const goRoute = useCallback(
    (raw: string) => {
      let p = raw.trim();
      if (!p) p = "/";
      if (!p.startsWith("/")) p = "/" + p;
      setNavStack((s) => [...s.slice(0, navIdx + 1), p]);
      setNavIdx((i) => i + 1);
    },
    [navIdx],
  );
  const canBack = navIdx > 0;
  const canFwd = navIdx < navStack.length - 1;
  const liveSrc = `${getBackendBaseURL()}${prefix}${route}`;

  // Live VNC view of the sandbox's real browser (watch the agent browse).
  const [showVnc, setShowVnc] = useState(false);
  // Gate on `active` as well as `showVnc`: tabs stay mounted (only `hidden` via
  // CSS), so once VNC was toggled on the noVNC iframe kept streaming video —
  // bandwidth and CPU — while the user sat on Files or Terminal.
  const vncVisible = Boolean(active) && showVnc;
  const { vnc: vncUrl } = useSandboxTerminalUrl(threadId, vncVisible);

  // Agent browser self-test (native sandbox Chromium): console errors + screenshot.
  const {
    result: manualTest,
    running: selfTesting,
    run: runSelfTest,
  } = useBrowserCheck(threadId);
  // The deterministic auto-check runs on every preview — poll it so results show
  // without anyone clicking. Manual run (if any) takes precedence.
  const autoTest = useLastBrowserCheck(threadId, active && devServer.running);
  const selfTest =
    manualTest ?? (autoTest && autoTest.routes.length > 0 ? autoTest : null);
  const [showSelfTest, setShowSelfTest] = useState(false);
  // Surface automatically when the self-test found issues.
  useEffect(() => {
    if (selfTest && !selfTest.ok && selfTest.routes.length > 0)
      setShowSelfTest(true);
  }, [selfTest]);
  const triggerSelfTest = useCallback(() => {
    setShowSelfTest(true);
    void runSelfTest(selectedLabel, route);
  }, [runSelfTest, selectedLabel, route]);

  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!content || !isHtml) {
      setBlobUrl(null);
      return;
    }
    const blob = new Blob([content], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    setBlobUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [content, isHtml]);

  // Download the HTML file (blob URL is same-origin; never window.open it).
  const openInNewTab = useCallback(() => {
    if (!content || !filename) return;
    const blob = new Blob([content], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }, [content, filename]);

  // Open the LIVE dev server preview in a real tab (proxy sets CSP sandbox + strips cookies).
  // noopener/noreferrer: the opened page is agent-authored/uncontrolled content —
  // without it, JS running there gets a `window.opener` handle back to this tab
  // and can navigate it (reverse tabnabbing), undermining the sandboxing already
  // applied to the equivalent iframe below.
  const openLiveInNewTab = useCallback(() => {
    if (devServer.url) window.open(liveSrc, "_blank", "noopener,noreferrer");
  }, [devServer.url, liveSrc]);

  // Pseudo-HMR: auto-reload the preview iframe each time the dev server recompiles.
  const compiles = devServer.compiles ?? 0;
  useEffect(() => {
    if (compiles > 0) setReloadKey((k) => k + 1);
  }, [compiles]);

  // Always-on preview: when a runnable project exists but no server is up, bring
  // it up deterministically (find project → install → start) — no click, no LLM.
  // Fire once per thread; if it stops later the user can use the manual button.
  const autoStartedRef = useRef(false);
  // The Browser tab isn't remounted per-thread (only the panel is), so without
  // this reset, switching to a second thread with `autoStartedRef.current`
  // already `true` from the first thread silently skips auto-start there.
  useEffect(() => {
    autoStartedRef.current = false;
  }, [threadId]);
  useEffect(() => {
    if (
      hasRunnableProject &&
      !devServer.running &&
      onStartPreview &&
      !autoStartedRef.current
    ) {
      autoStartedRef.current = true;
      void onStartPreview(selectedLabel);
    }
    if (devServer.running) autoStartedRef.current = true;
  }, [hasRunnableProject, devServer.running, onStartPreview, selectedLabel]);

  // ── VNC live view: watch the agent's real browser ──
  if (showVnc) {
    return (
      <div className="flex h-full flex-col">
        <div className="border-border/30 bg-muted/20 flex shrink-0 items-center gap-1.5 border-b px-2 py-1">
          <button
            onClick={() => setShowVnc(false)}
            className="text-muted-foreground hover:text-foreground rounded px-1 text-sm"
            title={t.agentComputer.browser.back}
          >
            ‹
          </button>
          <span className="text-muted-foreground/70 font-mono text-xs">
            {t.agentComputer.browser.watchLiveBrowser} · VNC
          </span>
        </div>
        <div className="min-h-0 flex-1 bg-black">
          {vncUrl && vncVisible ? (
            <iframe
              key={vncUrl}
              src={vncUrl}
              title={t.agentComputer.browser.watchAgentBrowser}
              className="h-full w-full border-0"
            />
          ) : (
            <div className="flex h-full items-center justify-center">
              <LoaderCircleIcon className="text-muted-foreground/30 h-5 w-5 animate-spin" />
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── HIGHEST PRIORITY: live dev server is running → iframe the proxy ──
  if (devServer.running && devServer.url) {
    return (
      <div className="flex h-full flex-col">
        {/* Browser chrome: nav + address bar */}
        <div className="border-border/30 bg-muted/20 flex shrink-0 items-center gap-1 border-b px-1.5 py-1">
          <button
            onClick={() => canBack && setNavIdx((i) => i - 1)}
            disabled={!canBack}
            className={cn(
              "rounded px-1 text-sm transition-colors",
              canBack
                ? "text-muted-foreground hover:text-foreground"
                : "text-muted-foreground/25",
            )}
            title={t.agentComputer.browser.back}
          >
            ‹
          </button>
          <button
            onClick={() => canFwd && setNavIdx((i) => i + 1)}
            disabled={!canFwd}
            className={cn(
              "rounded px-1 text-sm transition-colors",
              canFwd
                ? "text-muted-foreground hover:text-foreground"
                : "text-muted-foreground/25",
            )}
            title={t.agentComputer.browser.forward}
          >
            ›
          </button>
          <button
            onClick={() => setReloadKey((k) => k + 1)}
            className="text-muted-foreground/60 hover:text-foreground rounded px-1 transition-colors"
            title={t.agentComputer.browser.reload}
          >
            ⟳
          </button>
          <span
            className={cn(
              "ml-0.5 h-2 w-2 shrink-0 rounded-full",
              devServer.status === "ready"
                ? "bg-emerald-400"
                : "animate-pulse bg-yellow-400",
            )}
            title={
              devServer.status === "ready"
                ? t.agentComputer.browser.live
                : t.agentComputer.browser.compiling
            }
          />
          <form
            onSubmit={(e) => {
              e.preventDefault();
              goRoute(routeInput);
            }}
            className="border-border/40 bg-background/40 flex min-w-0 flex-1 items-center rounded-md border px-2"
          >
            <span className="text-muted-foreground/40 shrink-0 font-mono text-[10px] select-none">
              :{devServer.port}
            </span>
            <input
              value={routeInput}
              onChange={(e) => setRouteInput(e.target.value)}
              spellCheck={false}
              className="text-muted-foreground/90 focus:text-foreground min-w-0 flex-1 bg-transparent px-1.5 py-0.5 font-mono text-[11px] focus:outline-none"
              placeholder="/"
            />
          </form>
          {devServers.length > 1 && (
            <select
              value={selectedLabel}
              onChange={(e) => onSelectLabel?.(e.target.value)}
              className="border-border/40 bg-muted/30 text-muted-foreground shrink-0 rounded border px-1 py-0.5 font-mono text-[10px] focus:outline-none"
              title={t.agentComputer.browser.switchPreview}
            >
              {devServers.map((s) => (
                <option key={s.label} value={s.label}>
                  {s.label}
                </option>
              ))}
            </select>
          )}
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              onClick={triggerSelfTest}
              disabled={selfTesting}
              className={cn(
                "rounded p-1 transition-colors",
                selfTesting
                  ? "text-primary"
                  : selfTest && selfTest.routes.length > 0
                    ? selfTest.ok
                      ? "text-emerald-400"
                      : "text-red-400"
                    : "text-muted-foreground/50 hover:text-muted-foreground",
              )}
              title={t.agentComputer.browser.selfTest}
            >
              {selfTesting ? (
                <LoaderCircleIcon className="h-3 w-3 animate-spin" />
              ) : (
                <EyeIcon className="h-3 w-3" />
              )}
            </button>
            <button
              onClick={() => setShowVnc(true)}
              className="text-muted-foreground/50 hover:text-muted-foreground rounded px-1 py-0.5 font-mono text-[9px] transition-colors"
              title={t.agentComputer.browser.watchAgentBrowser}
            >
              VNC
            </button>
            <button
              onClick={() => setViewMode("desktop")}
              className={cn(
                "rounded p-1 transition-colors",
                viewMode === "desktop"
                  ? "text-foreground bg-muted"
                  : "text-muted-foreground/50 hover:text-muted-foreground",
              )}
              title={t.agentComputer.browser.desktop}
            >
              <MonitorIcon className="h-3 w-3" />
            </button>
            <button
              onClick={() => setViewMode("mobile")}
              className={cn(
                "rounded p-1 transition-colors",
                viewMode === "mobile"
                  ? "text-foreground bg-muted"
                  : "text-muted-foreground/50 hover:text-muted-foreground",
              )}
              title={t.agentComputer.browser.mobile}
            >
              <span className="font-mono text-[10px]">📱</span>
            </button>
            <button
              onClick={openLiveInNewTab}
              className="text-muted-foreground/50 hover:text-muted-foreground rounded p-1 transition-colors"
              title={t.agentComputer.browser.openNewTab}
            >
              <ExternalLinkIcon className="h-3 w-3" />
            </button>
          </div>
        </div>
        {/* Self-test results strip */}
        {showSelfTest && (
          <div className="border-border/30 bg-muted/10 shrink-0 border-b px-2 py-1.5 text-[11px]">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "font-medium",
                  selfTesting
                    ? "text-primary"
                    : selfTest?.ok
                      ? "text-emerald-400"
                      : "text-red-400",
                )}
              >
                {selfTesting
                  ? t.agentComputer.browser.testingInBrowser
                  : selfTest?.ok
                    ? t.agentComputer.browser.selfTestPassed
                    : t.agentComputer.browser.selfTestIssues}
              </span>
              {selfTest?.port != null && (
                <span className="text-muted-foreground/50 font-mono text-[10px]">
                  {t.agentComputer.browser.testedPort(String(selfTest.port))}
                </span>
              )}
              {selfTest &&
                !selfTest.ok &&
                selfTest.reason &&
                selfTest.routes.length === 0 && (
                  <span className="text-muted-foreground/60">
                    {selfTest.reason}
                  </span>
                )}
              <button
                onClick={() => setShowSelfTest(false)}
                className="text-muted-foreground/40 hover:text-muted-foreground ml-auto"
              >
                <XIcon className="h-3 w-3" />
              </button>
            </div>
            {(selfTest?.routes ?? []).map((r, i) => (
              <div key={i} className="mt-1 flex items-start gap-2">
                {r.screenshot && (
                  <a
                    href={r.screenshot}
                    target="_blank"
                    rel="noreferrer"
                    className="shrink-0"
                  >
                    {}
                    <img
                      src={r.screenshot}
                      alt={`screenshot ${r.route}`}
                      className="border-border/40 h-14 w-24 rounded border object-cover object-top"
                    />
                  </a>
                )}
                <div className="min-w-0 flex-1">
                  <span
                    className={cn(
                      "font-mono",
                      r.ok ? "text-emerald-400" : "text-red-400",
                    )}
                  >
                    {r.ok ? "✓" : "✗"} {r.route}
                  </span>
                  <span className="text-muted-foreground/50 ml-1">
                    [{r.status}]
                  </span>
                  {r.console_errors.slice(0, 3).map((ce, j) => (
                    <div
                      key={j}
                      className="truncate font-mono text-[10px] text-red-300/80"
                      title={ce}
                    >
                      {ce}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        <div
          className={cn(
            "bg-muted/10 flex min-h-0 flex-1 justify-center overflow-hidden",
            viewMode === "mobile" ? "py-2" : "",
          )}
        >
          {devServer.status === "ready" ? (
            <iframe
              key={`${reloadKey}-${navIdx}`}
              src={liveSrc}
              title={t.agentComputer.browser.livePreview}
              // No allow-same-origin: the preview is served on the app origin, so an
              // opaque-origin sandbox prevents the agent-built app from reaching the
              // parent app's cookies / localStorage / auth.
              sandbox="allow-scripts allow-forms allow-popups allow-modals"
              className={cn(
                "border-0 bg-white",
                viewMode === "mobile"
                  ? "h-full w-[375px] rounded-lg shadow-lg"
                  : "h-full w-full",
              )}
            />
          ) : devServer.status === "error" || devServer.status === "stopped" ? (
            <div className="flex h-full w-full flex-col items-center justify-center gap-3 px-4 text-center">
              <AlertTriangleIcon className="text-destructive/60 h-6 w-6" />
              <p className="text-muted-foreground/70 text-xs">
                {t.agentComputer.browser.devServerError}
              </p>
              {onStartPreview && (
                <button
                  onClick={() => void onStartPreview(selectedLabel)}
                  className="border-border/40 text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition-colors hover:border-[--primary]/40"
                >
                  <RefreshCwIcon className="h-3 w-3" />
                  {t.agentComputer.browser.retryPreview}
                </button>
              )}
            </div>
          ) : (
            <div className="flex h-full w-full flex-col items-center justify-center gap-3">
              <LoaderCircleIcon className="text-muted-foreground/40 h-6 w-6 animate-spin" />
              <p className="text-muted-foreground/50 text-xs">
                {t.agentComputer.browser.devServerCompiling}
              </p>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (!filePath) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
        <GlobeIcon className="text-muted-foreground/20 h-8 w-8" />
        <p className="text-muted-foreground/50 text-xs">
          {t.agentComputer.browser.previewWillAppear}
          <br />
          {t.agentComputer.browser.previewWillAppearLine2}
        </p>
        <button
          onClick={() => setShowVnc(true)}
          className="border-border/40 text-muted-foreground hover:text-foreground mt-1 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition-colors hover:border-[--primary]/40"
        >
          <EyeIcon className="h-3 w-3" />{" "}
          {t.agentComputer.browser.watchLiveBrowser}
        </button>
      </div>
    );
  }

  if (!isHtml) {
    // Non-HTML project (Next.js, React, etc.) — show project info instead
    const ext = filename.split(".").at(-1)?.toLowerCase() ?? "";
    const projectType = ["tsx", "ts", "jsx", "js"].includes(ext)
      ? t.agentComputer.browser.projectType.react
      : ext === "py"
        ? t.agentComputer.browser.projectType.python
        : ext === "md"
          ? t.agentComputer.browser.projectType.markdown
          : t.agentComputer.browser.projectType.code;
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
        <CodeIcon className="h-8 w-8 text-blue-400/30" />
        <div>
          <p className="text-muted-foreground text-xs font-medium">
            {t.agentComputer.browser.projectLabel(projectType)}
          </p>
          <p className="text-muted-foreground/60 mt-1 font-mono text-[11px]">
            {filename}
          </p>
          <p className="text-muted-foreground/50 mt-2 text-[11px] leading-relaxed">
            {t.agentComputer.browser.switchToEditorPrefix}{" "}
            <span className="text-blue-400">{t.agentComputer.tabs.editor}</span>{" "}
            {t.agentComputer.browser.switchToEditorSuffix}
          </p>
          {(onStartPreview ?? onAgentMessage) && (
            <button
              onClick={() => {
                // Deterministic path: backend runs find-project → install → start.
                // Fall back to nudging the agent only if the runner isn't wired.
                if (onStartPreview) void onStartPreview(selectedLabel);
                else
                  onAgentMessage?.(
                    `Start the dev server (npm install if needed, then start_dev_server) so I can preview the running app in the ${t.agentComputer.tabs.browser} tab.`,
                  );
              }}
              className="bg-primary text-primary-foreground mt-3 inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-opacity hover:opacity-90"
            >
              <GlobeIcon className="h-3 w-3" />
              {t.agentComputer.browser.startLivePreview}
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Browser toolbar */}
      <div className="border-border/30 bg-muted/20 flex shrink-0 items-center gap-1.5 border-b px-2 py-1">
        <GlobeIcon className="h-3 w-3 shrink-0 text-orange-400" />
        <span className="text-muted-foreground/70 min-w-0 flex-1 truncate font-mono text-xs">
          {filename}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <button
            onClick={() => setViewMode("desktop")}
            className={cn(
              "rounded p-1 transition-colors",
              viewMode === "desktop"
                ? "text-foreground bg-muted"
                : "text-muted-foreground/50 hover:text-muted-foreground",
            )}
          >
            <MonitorIcon className="h-3 w-3" />
          </button>
          <button
            onClick={() => setViewMode("mobile")}
            className={cn(
              "rounded p-1 transition-colors",
              viewMode === "mobile"
                ? "text-foreground bg-muted"
                : "text-muted-foreground/50 hover:text-muted-foreground",
            )}
          >
            <span className="font-mono text-[10px]">📱</span>
          </button>
          <button
            onClick={openInNewTab}
            className="text-muted-foreground/50 hover:text-muted-foreground rounded p-1 transition-colors"
            title={t.agentComputer.browser.openNewTab}
          >
            <DownloadIcon className="h-3 w-3" />
          </button>
        </div>
      </div>
      {/* iframe */}
      <div
        className={cn(
          "bg-muted/10 flex min-h-0 flex-1 justify-center overflow-hidden",
          viewMode === "mobile" ? "py-2" : "",
        )}
      >
        {blobUrl ? (
          <iframe
            key={blobUrl}
            src={blobUrl}
            sandbox="allow-scripts allow-forms"
            title={`Preview: ${filename}`}
            className={cn(
              "border-0 bg-white",
              viewMode === "mobile"
                ? "h-full w-[375px] rounded-lg shadow-lg"
                : "h-full w-full",
            )}
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-4 text-center">
            {isLoading ? (
              <LoaderCircleIcon className="text-muted-foreground/30 h-5 w-5 animate-spin" />
            ) : exists ? (
              // Fetched, and the file really is empty (or not HTML). Spinning
              // here waited forever on a 0-byte deliverable.
              <p className="text-muted-foreground/60 text-xs">
                {t.agentComputer.browser.fileEmpty(filename)}
              </p>
            ) : (
              <>
                <GlobeIcon className="text-muted-foreground/20 h-6 w-6" />
                <p className="text-muted-foreground/60 text-xs">
                  {t.agentComputer.browser.fileMissing(filename)}
                </p>
                {onStartPreview && (
                  <button
                    onClick={() => void onStartPreview(selectedLabel)}
                    className="border-border/40 text-muted-foreground hover:text-foreground mt-1 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition-colors"
                  >
                    <GlobeIcon className="h-3 w-3" />
                    {t.agentComputer.browser.startLivePreview}
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
