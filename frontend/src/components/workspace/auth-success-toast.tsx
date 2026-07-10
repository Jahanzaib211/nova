"use client";

import { useEffect } from "react";
import { toast } from "sonner";

/**
 * One-shot success toast after login/signup. The auth form can't toast
 * directly — the Toaster only mounts inside the workspace, so a toast fired
 * on /login or /signup is dropped during the navigation. The form instead
 * leaves a flag in sessionStorage, and this component (mounted next to the
 * Toaster) consumes it exactly once on arrival.
 */
export function AuthSuccessToast() {
  useEffect(() => {
    const flag = sessionStorage.getItem("nova:auth-success");
    if (!flag) return;
    sessionStorage.removeItem("nova:auth-success");
    toast.success(
      flag === "signup"
        ? "Account created — welcome to Nova!"
        : "Signed in successfully. Welcome back!",
    );
  }, []);

  return null;
}
