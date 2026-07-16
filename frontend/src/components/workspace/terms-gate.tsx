"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";

/**
 * Blocking consent gate. When the signed-in user has not accepted the current
 * Terms version, this overlays the workspace and requires acceptance before
 * anything else can be used. Enforces consent server-side (the accept call
 * stamps the version + timestamp) rather than relying only on the signup form.
 */
export function TermsGate() {
  const { user, refreshUser } = useAuth();
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/v1/legal/terms", { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { tos_version?: string } | null) => {
        if (data?.tos_version) setCurrentVersion(data.tos_version);
      })
      .catch(() => {
        // Fail open: if we can't fetch the version, don't block the app.
      });
    return () => controller.abort();
  }, []);

  const needsAccept =
    !!user &&
    !!currentVersion &&
    user.tos_accepted_version !== currentVersion;

  if (!needsAccept) return null;

  const handleAccept = async () => {
    setSubmitting(true);
    setError(false);
    try {
      const res = await fetch("/api/v1/legal/accept", {
        method: "POST",
        headers: { ...getCsrfHeaders() },
      });
      if (!res.ok) throw new Error(`accept ${res.status}`);
      await refreshUser();
    } catch {
      setError(true);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="bg-background w-full max-w-md space-y-5 rounded-2xl border p-7 shadow-xl">
        <div>
          <h2 className="text-foreground text-xl font-semibold">
            We&rsquo;ve updated our terms
          </h2>
          <p className="text-muted-foreground mt-2 text-sm leading-relaxed">
            To keep using Nova, please review and accept the latest{" "}
            <a
              href="/terms"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-500 hover:underline"
            >
              Terms of Service
            </a>{" "}
            and{" "}
            <a
              href="/privacy"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-500 hover:underline"
            >
              Privacy Policy
            </a>
            .
          </p>
        </div>

        {error && (
          <p className="text-sm text-red-500">
            Something went wrong. Please try again.
          </p>
        )}

        <Button
          className="w-full"
          onClick={handleAccept}
          disabled={submitting}
        >
          {submitting ? "Saving…" : "I agree — continue"}
        </Button>
      </div>
    </div>
  );
}
