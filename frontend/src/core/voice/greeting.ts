/**
 * Nova saying hello.
 *
 * Two things are deliberately separate here:
 *
 * - `greetingFor()` is **pure** — given the moment and a name it returns words.
 *   Keeping it free of I/O means the copy can be unit-tested without a browser,
 *   an audio device, or a backend.
 * - `speak()` does the I/O and is honest about the one thing that reliably goes
 *   wrong: **browsers block audio that no user gesture asked for.** A fresh page
 *   load after login has no user activation, so autoplay is usually refused.
 *   Rather than pretending otherwise, `speak()` reports whether sound actually
 *   started, and the caller offers a click-to-hear affordance when it did not.
 */

import { fetch as apiFetch } from "@/core/api/fetcher";

export type GreetingKind = "signup" | "login" | "return";

/** Time-of-day band, injectable so tests are not clock-dependent. */
export function partOfDay(at: Date = new Date()): "morning" | "afternoon" | "evening" {
  const h = at.getHours();
  if (h < 12) return "morning";
  if (h < 18) return "afternoon";
  return "evening";
}

/**
 * Best-effort first name from an email address.
 *
 * Nova only knows your email — reading `alijatt2323@gmail.com` aloud would be
 * worse than saying nothing, so the local part is stripped of digits and
 * separators. Returns `null` whenever the result stops looking like a name
 * (too short, or an alias like `admin` / `no-reply`), because an awkward guess
 * spoken out loud is more jarring than a plain "Good evening".
 */
export function displayNameFrom(email?: string | null): string | null {
  const local = email?.split("@")[0] ?? "";
  // Split on separators, keep the first alphabetic run.
  const head = local.split(/[._+-]/)[0]?.replace(/[^a-zA-Z]/g, "") ?? "";
  if (head.length < 3) return null;
  const generic = new Set(["admin", "info", "test", "user", "mail", "noreply", "support", "contact", "hello"]);
  if (generic.has(head.toLowerCase())) return null;
  return head[0]!.toUpperCase() + head.slice(1).toLowerCase();
}

/**
 * The words Nova greets you with.
 *
 * Kept short on purpose: this is spoken aloud, and anything longer than a
 * sentence stops being a greeting and starts being an interruption.
 */
export function greetingFor(
  kind: GreetingKind,
  name?: string | null,
  at: Date = new Date(),
): string {
  const who = name?.trim() ? `, ${name.trim().split(/\s+/)[0]}` : "";
  switch (kind) {
    case "signup":
      return `Welcome to Nova${who}. I'm online and ready when you are.`;
    case "login":
      return `Good ${partOfDay(at)}${who}. Nova here — what are we building today?`;
    default:
      return `Welcome back${who}. Ready when you are.`;
  }
}

export type SpeakResult =
  | { status: "played"; stop: () => void }
  | { status: "blocked"; play: () => Promise<void> }
  | { status: "unavailable" };

/**
 * Synthesize `text` on the server and play it.
 *
 * Returns `"blocked"` — with a `play()` you can wire to a button — when the
 * audio was fetched fine but the browser refused to start it without a gesture.
 * That is not an error and must not be reported as one; it is the default
 * behaviour of every modern browser.
 */
export async function speak(text: string, signal?: AbortSignal): Promise<SpeakResult> {
  let url: string | null = null;
  try {
    const res = await apiFetch("/api/voice/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal,
    });
    if (!res.ok) return { status: "unavailable" };

    const blob = await res.blob();
    if (!blob.size) return { status: "unavailable" };
    url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    // Release the blob whichever way playback ends, or this leaks a few hundred
    // KB per greeting for the lifetime of the tab.
    const revoke = () => {
      if (url) URL.revokeObjectURL(url);
      url = null;
    };
    audio.addEventListener("ended", revoke, { once: true });
    audio.addEventListener("error", revoke, { once: true });

    const stop = () => {
      audio.pause();
      revoke();
    };

    try {
      await audio.play();
      return { status: "played", stop };
    } catch {
      // Autoplay refused. Hand back a play() that will succeed once it is
      // called from inside a real click handler.
      return {
        status: "blocked",
        play: async () => {
          await audio.play();
        },
      };
    }
  } catch {
    if (url) URL.revokeObjectURL(url);
    return { status: "unavailable" };
  }
}
