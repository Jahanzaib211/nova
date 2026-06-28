"use client";

/**
 * Next.js global-error boundary — handles fatal render errors that escape
 * the root <ErrorBoundary>. Renders a standalone HTML document because the
 * root layout is replaced by this component when it triggers. The
 * simplified markup intentionally avoids app providers (no i18n, no
 * theme) so the page is guaranteed to render even if those providers
 * are the cause of the failure.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily:
            "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          background: "#0a0a0a",
          color: "#fafafa",
        }}
      >
        <div style={{ maxWidth: 480, padding: 32, textAlign: "center" }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>
            Something went wrong
          </h1>
          <p
            style={{
              marginTop: 12,
              color: "#a1a1aa",
              fontSize: 14,
              lineHeight: 1.5,
            }}
          >
            A fatal error occurred while rendering this page. Reload to retry,
            or check the server logs for the stack trace below.
          </p>
          {error.digest && (
            <code
              style={{
                display: "inline-block",
                marginTop: 16,
                padding: "4px 8px",
                borderRadius: 4,
                background: "#18181b",
                fontSize: 11,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                color: "#a1a1aa",
              }}
            >
              {error.digest}
            </code>
          )}
          <div
            style={{
              marginTop: 24,
              display: "flex",
              gap: 8,
              justifyContent: "center",
            }}
          >
            <button
              type="button"
              onClick={reset}
              style={{
                padding: "6px 14px",
                border: "1px solid #27272a",
                borderRadius: 6,
                background: "transparent",
                color: "#fafafa",
                fontSize: 13,
                cursor: "pointer",
              }}
            >
              Try again
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              style={{
                padding: "6px 14px",
                border: "1px solid #3f3f46",
                borderRadius: 6,
                background: "#fafafa",
                color: "#0a0a0a",
                fontSize: 13,
                cursor: "pointer",
              }}
            >
              Reload
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
