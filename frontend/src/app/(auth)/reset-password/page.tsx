"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetch as apiFetch } from "@/core/api/fetcher";
import { useI18n } from "@/core/i18n/hooks";

import { AuthShell } from "../auth-shell";

export default function ResetPasswordPage() {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (password !== confirmPassword) {
      setError(t.authPasswordReset.passwordsDontMatch);
      return;
    }

    setLoading(true);

    try {
      const res = await apiFetch("/api/v1/auth/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: password }),
        credentials: "include",
      });

      const data = (await res.json().catch(() => ({}))) as {
        detail?: { code?: string };
      };
      if (!res.ok) {
        if (data.detail?.code === "password_reset_token_invalid") {
          setError(t.authPasswordReset.resetLinkInvalid);
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
          {t.authPasswordReset.resetTitle}
        </p>
      </div>

      {done ? (
        <div className="space-y-4">
          <p role="status" className="text-center text-sm text-green-600">
            {t.authPasswordReset.resetSuccess}
          </p>
          <Button asChild className="w-full">
            <Link href="/login">{t.authPasswordReset.backToLogin}</Link>
          </Button>
        </div>
      ) : (
        <>
          <p className="text-muted-foreground text-center text-xs">
            {t.authPasswordReset.resetHint}
          </p>
          <form onSubmit={handleSubmit} className="space-y-2">
            <div className="flex flex-col space-y-1">
              <label htmlFor="password" className="text-sm font-medium">
                {t.authPasswordReset.newPassword}
              </label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="•••••••"
                required
                minLength={8}
              />
            </div>
            <div className="flex flex-col space-y-1">
              <label htmlFor="confirm-password" className="text-sm font-medium">
                {t.authPasswordReset.confirmNewPassword}
              </label>
              <Input
                id="confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="•••••••"
                required
                minLength={8}
              />
            </div>

            {error && (
              <p role="alert" className="text-sm text-red-500">
                {error}
              </p>
            )}

            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "…" : t.authPasswordReset.resetSubmit}
            </Button>
          </form>
        </>
      )}
    </AuthShell>
  );
}
