"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetch as apiFetch } from "@/core/api/fetcher";
import { useI18n } from "@/core/i18n/hooks";

import { AuthShell } from "../auth-shell";

export default function ForgotPasswordPage() {
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const res = await apiFetch("/api/v1/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
        credentials: "include",
      });

      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as {
          detail?: { code?: string };
        };
        if (data.detail?.code === "password_reset_disabled") {
          setError(t.authPasswordReset.disabled);
        } else {
          setError(t.authPasswordReset.errorGeneric);
        }
        return;
      }

      setDone(true);
    } catch {
      setError(t.authPasswordReset.errorGeneric);
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell>
      <div className="text-center">
        <h1 className="bg-gradient-to-r from-violet-600 to-cyan-600 bg-clip-text font-serif text-3xl font-semibold text-transparent dark:from-violet-400 dark:to-cyan-300">
          Nova
        </h1>
        <p className="text-muted-foreground mt-2">
          {t.authPasswordReset.forgotTitle}
        </p>
      </div>

      {done ? (
        <p role="status" className="text-center text-sm text-green-600">
          {t.authPasswordReset.sent}
        </p>
      ) : (
        <>
          <p className="text-muted-foreground text-center text-xs">
            {t.authPasswordReset.forgotHint}
          </p>
          <form onSubmit={handleSubmit} className="space-y-2">
            <div className="flex flex-col space-y-1">
              <label htmlFor="email" className="text-sm font-medium">
                {t.authPasswordReset.email}
              </label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
              />
            </div>

            {error && (
              <p role="alert" className="text-sm text-red-500">
                {error}
              </p>
            )}

            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "…" : t.authPasswordReset.sendResetLink}
            </Button>
          </form>
        </>
      )}

      <div className="text-center text-sm">
        <Link href="/login" className="text-blue-500 hover:underline">
          {t.authPasswordReset.backToLogin}
        </Link>
      </div>
    </AuthShell>
  );
}
