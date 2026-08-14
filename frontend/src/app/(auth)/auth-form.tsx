"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { FlickeringGrid } from "@/components/ui/flickering-grid";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/core/auth/AuthProvider";
import { parseAuthError } from "@/core/auth/types";

/**
 * Validate next parameter
 * Prevent open redirect attacks
 * Per RFC-001: Only allow relative paths starting with /
 */
function validateNextParam(next: string | null): string | null {
  if (!next) {
    return null;
  }

  // Need start with / (relative path)
  if (!next.startsWith("/")) {
    return null;
  }

  // Disallow protocol-relative URLs
  if (
    next.startsWith("//") ||
    next.startsWith("http://") ||
    next.startsWith("https://")
  ) {
    return null;
  }

  // Disallow URLs with different protocols (e.g., javascript:, data:, etc)
  if (next.includes(":") && !next.startsWith("/")) {
    return null;
  }

  // Valid relative path
  return next;
}

export type AuthMode = "login" | "signup";

/**
 * Shared login/signup form. Rendered by both /login and /signup so each has a
 * real, linkable route; the toggle at the bottom navigates between them
 * (preserving ?next=) instead of flipping local state, keeping the URL truthful.
 */
export function AuthForm({ initialMode }: { initialMode: AuthMode }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isAuthenticated } = useAuth();
  const { theme, resolvedTheme } = useTheme();

  const isLogin = initialMode === "login";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  // Get next parameter for validated redirect
  const nextParam = searchParams.get("next");
  const redirectPath = validateNextParam(nextParam) ?? "/workspace";

  // The other auth route, preserving ?next= so the post-auth destination
  // survives switching between sign-in and sign-up.
  const otherModeHref = `${isLogin ? "/signup" : "/login"}${
    validateNextParam(nextParam)
      ? `?next=${encodeURIComponent(nextParam!)}`
      : ""
  }`;

  // Redirect if already authenticated (client-side, post-login)
  useEffect(() => {
    if (isAuthenticated) {
      router.push(redirectPath);
    }
  }, [isAuthenticated, redirectPath, router]);

  // Redirect to setup if the system has no users yet
  useEffect(() => {
    let cancelled = false;

    void fetch("/api/v1/auth/setup-status")
      .then((r) => r.json())
      .then((data: { needs_setup?: boolean }) => {
        if (!cancelled && data.needs_setup) {
          router.push("/setup");
        }
      })
      .catch(() => {
        // Ignore errors; user stays on login page
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!isLogin && password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    if (!isLogin && !acceptedTerms) {
      setError("Please accept the Terms and Privacy Policy to continue.");
      return;
    }

    setLoading(true);

    try {
      const endpoint = isLogin
        ? "/api/v1/auth/login/local"
        : "/api/v1/auth/register";
      const body = isLogin
        ? `username=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`
        : JSON.stringify({
            email,
            password,
            accepted_terms: acceptedTerms,
            ...(searchParams.get("ref")
              ? { referred_by: searchParams.get("ref") }
              : {}),
          });

      const headers: HeadersInit = isLogin
        ? { "Content-Type": "application/x-www-form-urlencoded" }
        : { "Content-Type": "application/json" };

      const res = await fetch(endpoint, {
        method: "POST",
        headers,
        body,
        credentials: "include", // Important: include HttpOnly cookie
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      // Both login and register set a cookie — redirect to workspace.
      // The Toaster lives in the workspace, so leave a flag for it to show
      // the success toast on arrival; the inline message covers the gap.
      sessionStorage.setItem("nova:auth-success", isLogin ? "login" : "signup");
      setSuccess(true);
      router.push(redirectPath);
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const actualTheme = theme === "system" ? resolvedTheme : theme;

  return (
    <div className="bg-background relative flex min-h-screen items-center justify-center overflow-x-hidden overflow-y-auto">
      <FlickeringGrid
        className="absolute inset-0 z-0 mask-[url(/images/nova.svg)] mask-size-[100vw] mask-center mask-no-repeat md:mask-size-[72vh]"
        squareSize={4}
        gridGap={4}
        color={actualTheme === "dark" ? "white" : "black"}
        maxOpacity={0.3}
        flickerChance={0.25}
      />
      <div className="border-border/20 bg-background/5 w-full max-w-md space-y-6 rounded-3xl border p-8 backdrop-blur-sm">
        <div className="text-center">
          <h1 className="bg-gradient-to-r from-violet-600 to-cyan-600 bg-clip-text font-serif text-3xl font-semibold text-transparent dark:from-violet-400 dark:to-cyan-300">
            Nova
          </h1>
          <p className="text-muted-foreground mt-2">
            {isLogin ? "Sign in to your account" : "Create a new account"}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-2">
          <div className="flex flex-col space-y-1">
            <label htmlFor="email" className="text-sm font-medium">
              Email
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
          <div className="flex flex-col space-y-1">
            <label htmlFor="password" className="text-sm font-medium">
              Password
            </label>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="•••••••"
              required
              minLength={isLogin ? 6 : 8}
            />
            {!isLogin && (
              <p className="text-muted-foreground text-xs">
                At least 8 characters; common passwords are rejected.
              </p>
            )}
          </div>
          {!isLogin && (
            <div className="flex flex-col space-y-1">
              <label htmlFor="confirm-password" className="text-sm font-medium">
                Confirm password
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
          )}

          {!isLogin && (
            <label
              htmlFor="accept-terms"
              className="text-muted-foreground flex items-start gap-2 pt-1 text-xs"
            >
              <input
                id="accept-terms"
                type="checkbox"
                checked={acceptedTerms}
                onChange={(e) => setAcceptedTerms(e.target.checked)}
                className="mt-0.5 size-3.5 shrink-0 accent-violet-600"
                required
              />
              <span>
                I agree to the{" "}
                <Link
                  href="/terms"
                  target="_blank"
                  className="text-blue-500 hover:underline"
                >
                  Terms of Service
                </Link>{" "}
                and{" "}
                <Link
                  href="/privacy"
                  target="_blank"
                  className="text-blue-500 hover:underline"
                >
                  Privacy Policy
                </Link>
                .
              </span>
            </label>
          )}

          {error && (
            <p role="alert" aria-live="assertive" aria-atomic="true" className="text-sm text-red-500">
              {error}
            </p>
          )}
          {success && (
            <p role="status" aria-live="polite" aria-atomic="true" className="text-sm text-green-600">
              {isLogin
                ? "Signed in! Redirecting to your workspace…"
                : "Account created! Redirecting to your workspace…"}
            </p>
          )}

          <Button
            type="submit"
            className="w-full"
            disabled={loading || success}
          >
            {success
              ? "Redirecting…"
              : loading
                ? "Please wait..."
                : isLogin
                  ? "Sign In"
                  : "Create Account"}
          </Button>
        </form>

        <div className="text-center text-sm">
          <Link href={otherModeHref} className="text-blue-500 hover:underline">
            {isLogin
              ? "Don't have an account? Sign up"
              : "Already have an account? Sign in"}
          </Link>
        </div>

        {isLogin && (
          <div className="text-center text-xs">
            <Link
              href={`/forgot-password${
                validateNextParam(nextParam)
                  ? `?next=${encodeURIComponent(nextParam!)}`
                  : ""
              }`}
              className="text-blue-500 hover:underline"
            >
              Forgot your password?
            </Link>
          </div>
        )}

        <div className="text-muted-foreground text-center text-xs">
          <Link href="/" className="hover:underline">
            ← Back to home
          </Link>
        </div>

        <div className="text-muted-foreground/60 text-center text-[10px] tracking-wide">
          Made by Ali Technologies
        </div>
      </div>
    </div>
  );
}
