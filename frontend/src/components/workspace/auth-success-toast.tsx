"use client";

import { useEffect } from "react";
import { toast } from "sonner";

import { useAuth } from "@/core/auth/AuthProvider";
import {
  displayNameFrom,
  greetingFor,
  speak,
  type GreetingKind,
} from "@/core/voice/greeting";

/**
 * One-shot success toast after login/signup — and Nova's spoken hello.
 *
 * The auth form can't toast directly: the Toaster only mounts inside the
 * workspace, so a toast fired on /login or /signup is dropped during the
 * navigation. The form instead leaves a flag in sessionStorage, and this
 * component (mounted next to the Toaster) consumes it exactly once on arrival.
 *
 * The greeting is spoken through the one-shot `/api/voice/speak` endpoint
 * rather than the duplex session, so saying hello never prompts for microphone
 * permission. Asking for the mic before the user has asked for anything is the
 * fastest way to get that permission denied permanently.
 *
 * Autoplay is expected to be refused here — a fresh navigation carries no user
 * activation — so a refusal downgrades to a "Hear it" action on the toast
 * instead of being surfaced as a failure.
 */
export function AuthSuccessToast() {
  const { user } = useAuth();

  useEffect(() => {
    const flag = sessionStorage.getItem("nova:auth-success");
    if (!flag) return;
    sessionStorage.removeItem("nova:auth-success");

    const kind: GreetingKind = flag === "signup" ? "signup" : "login";
    const words = greetingFor(kind, displayNameFrom(user?.email));

    const controller = new AbortController();
    const id = toast.success(words, { duration: 6000 });

    void (async () => {
      const result = await speak(words, controller.signal);
      if (result.status === "blocked") {
        // Re-issue the same toast id so it updates in place rather than
        // stacking a second one on top of the first.
        toast.success(words, {
          id,
          duration: 10000,
          action: {
            label: "🔊 Hear it",
            onClick: () => {
              void result.play();
            },
          },
        });
      }
    })();

    return () => controller.abort();
    // Intentionally runs once on mount: the sessionStorage flag is the trigger,
    // and re-running when `user` resolves would greet twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}
