"use client";

import { AlertTriangleIcon, RotateCcwIcon } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

import { cn } from "@/lib/utils";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Human-readable tab name shown in the fallback message. */
  tabName: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Tab-scoped React error boundary for the Agent's Computer panel.
 *
 * A single tab crashing must not take down the other 5 tabs. Each tab's
 * content is wrapped in one of these; on render error it shows a small
 * inline fallback with a Reset button that clears local state and
 * re-renders.
 *
 * Logs the error to the browser console for debugging. Does NOT report
 * to any external service (privacy / no third-party tracking).
 */
export class AgentComputerErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    if (typeof console !== "undefined") {
      console.error(`[AgentComputerErrorBoundary] tab="${this.props.tabName}" crashed:`, error, info);
    }
  }

  handleReset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    const { hasError, error } = this.state;
    const { children, tabName } = this.props;

    if (!hasError) return children;

    return (
      <div
        role="alert"
        aria-live="polite"
        className={cn(
          "flex h-full flex-col items-center justify-center gap-3 px-6 py-8 text-center",
          "border border-amber-500/30 bg-amber-500/5",
        )}
      >
        <AlertTriangleIcon className="h-6 w-6 text-amber-400" aria-hidden />
        <div className="space-y-1">
          <div className="font-mono text-sm font-medium text-amber-300">
            {tabName} tab crashed
          </div>
          <div className="text-xs text-muted-foreground/70">
            The other tabs are unaffected. Reset to retry, or refresh the page if this persists.
          </div>
        </div>
        {error?.message ? (
          <pre className="max-w-full overflow-x-auto rounded border border-border/30 bg-muted/30 px-3 py-2 text-left font-mono text-[11px] text-muted-foreground/80">
            {error.message}
          </pre>
        ) : null}
        <button
          type="button"
          onClick={this.handleReset}
          className={cn(
            "inline-flex items-center gap-1.5 rounded border border-border/40 bg-background/60 px-3 py-1.5",
            "text-xs font-medium text-foreground/90 hover:bg-muted/40 transition-colors",
            "focus:outline-none focus:ring-2 focus:ring-amber-500/40",
          )}
        >
          <RotateCcwIcon className="h-3 w-3" aria-hidden />
          Reset tab
        </button>
      </div>
    );
  }
}
