"use client";

import { AlertTriangleIcon, RotateCcwIcon } from "lucide-react";
import { Component, Fragment, type ErrorInfo, type ReactNode } from "react";

import { cn } from "@/lib/utils";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Human-readable tab name shown in the fallback message. */
  tabName: string;
  /**
   * When any value changes, the boundary resets and REMOUNTS the subtree.
   * The panel passes `[threadId]`: a crash triggered by thread A's data must
   * never survive into thread B as a stuck "tab crashed" screen.
   */
  resetKeys?: unknown[];
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  /** Bumped on every reset; used as a React key so children fully remount. */
  resetCount: number;
}

/**
 * Tab-scoped React error boundary for the Agent's Computer panel.
 *
 * A single tab crashing must not take down the other 5 tabs. Each tab's
 * content is wrapped in one of these; on render error it shows a small
 * inline fallback with a Reset button.
 *
 * Reset actually remounts the children (they render under a `key` that
 * changes with every reset). Clearing the flag alone re-rendered the SAME
 * instance — whatever hook state threw in the first place was still there,
 * so the tab instantly re-crashed and "Reset" looked broken.
 *
 * Logs the error to the browser console for debugging. Does NOT report
 * to any external service (privacy / no third-party tracking).
 */
export class AgentComputerErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { hasError: false, error: null, resetCount: 0 };

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { hasError: true, error };
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps): void {
    const prev = prevProps.resetKeys ?? [];
    const next = this.props.resetKeys ?? [];
    if (
      !this.state.hasError ||
      prev.length !== next.length ||
      next.some((v, i) => v !== prev[i])
    ) {
      return;
    }
    // A resetKey changed while showing the fallback (thread switch): recover
    // automatically instead of keeping a stale crash screen.
    this.setState((s) => ({
      hasError: false,
      error: null,
      resetCount: s.resetCount + 1,
    }));
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    if (typeof console !== "undefined") {
      console.error(
        `[AgentComputerErrorBoundary] tab="${this.props.tabName}" crashed:`,
        error,
        info,
      );
    }
  }

  handleReset = (): void => {
    this.setState((s) => ({
      hasError: false,
      error: null,
      resetCount: s.resetCount + 1,
    }));
  };

  render(): ReactNode {
    const { hasError, error, resetCount } = this.state;
    const { children, tabName } = this.props;

    if (!hasError) return <Fragment key={resetCount}>{children}</Fragment>;

    return (
      <Fragment key={resetCount}>
        <div
          role="alert"
          aria-live="polite"
          className={cn(
            "flex h-full flex-col items-center justify-center gap-3 px-6 py-8 text-center",
            "border-warning/30 bg-warning/5 border",
          )}
        >
          <AlertTriangleIcon className="text-warning h-6 w-6" aria-hidden />
          <div className="space-y-1">
            <div className="text-warning font-mono text-sm font-medium">
              {tabName} tab crashed
            </div>
            <div className="text-muted-foreground/70 text-xs">
              The other tabs are unaffected. Reset to retry, or refresh the page
              if this persists.
            </div>
          </div>
          {error?.message ? (
            <pre className="border-panel-border bg-muted/30 text-muted-foreground/80 max-w-full overflow-x-auto rounded border px-3 py-2 text-left font-mono text-[11px]">
              {error.message}
            </pre>
          ) : null}
          <button
            type="button"
            onClick={this.handleReset}
            className={cn(
              "border-panel-border bg-background/60 inline-flex items-center gap-1.5 rounded border px-3 py-1.5",
              "text-foreground/90 hover:bg-muted/40 text-xs font-medium transition-colors",
              "focus:ring-warning/40 focus:ring-2 focus:outline-none",
            )}
          >
            <RotateCcwIcon className="h-3 w-3" aria-hidden />
            Reset tab
          </button>
        </div>
      </Fragment>
    );
  }
}
