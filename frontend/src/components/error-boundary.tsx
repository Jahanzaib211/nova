"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Optional fallback override. Defaults to the inline error panel below. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
  /** Optional scoped label — surfaces in error reports. */
  scope?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Last-line error gate for any subtree. Re-throws during render so
 * Next.js's production error overlay still fires; in dev the React
 * error overlay takes over for HMR.
 *
 * Wrap whole routes with this so a throw in a deep component never
 * white-screens the app. `scope` is reported to the console so
 * attribution stays clear when multiple boundaries are nested.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep the dev-tools error stream the source of truth — no console
    // duplication here. The error overlay picks this up automatically.
    if (process.env.NODE_ENV !== "production") {
      console.error(
        `[ErrorBoundary${this.props.scope ? `:${this.props.scope}` : ""}]`,
        error,
        info.componentStack,
      );
    }
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (error) {
      if (this.props.fallback) {
        return this.props.fallback(error, this.reset);
      }
      return <DefaultErrorPanel error={error} reset={this.reset} />;
    }
    return this.props.children;
  }
}

interface ErrorPanelProps {
  error: Error;
  reset: () => void;
}

function DefaultErrorPanel({ error, reset }: ErrorPanelProps): ReactNode {
  // Render nothing when there's no DOM (e.g. SSR) — the parent ErrorBoundary
  // re-throws on the server so Next.js shows its own error page.
  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Something went wrong</h2>
        <p className="max-w-md text-sm text-muted-foreground">
          An unexpected error occurred. The team has been notified. You can retry the action,
          or refresh the page to start fresh.
        </p>
        {process.env.NODE_ENV !== "production" && (
          <pre className="mx-auto mt-4 max-w-2xl overflow-auto rounded-md bg-muted/40 p-3 text-left text-xs text-muted-foreground">
            {error.name}: {error.message}
          </pre>
        )}
      </div>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={reset}>
          Try again
        </Button>
        <Button variant="default" size="sm" onClick={() => window.location.reload()}>
          Refresh page
        </Button>
      </div>
    </div>
  );
}
