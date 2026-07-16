import { type Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy Policy — Nova",
  description:
    "How Nova, the computer agent by Ali Technologies, collects and handles your data.",
};

// Keep in sync with backend app/gateway/legal.py PRIVACY_VERSION.
const PRIVACY_VERSION = "2026-07-15";

export default function PrivacyPage() {
  return (
    <main className="bg-background min-h-screen">
      <div className="mx-auto max-w-3xl px-6 py-16">
        <Link
          href="/"
          className="bg-gradient-to-r from-violet-600 to-cyan-600 bg-clip-text font-serif text-2xl font-semibold tracking-wide text-transparent dark:from-violet-400 dark:to-cyan-300"
        >
          Nova
        </Link>
        <h1 className="text-foreground mt-6 text-4xl font-semibold tracking-tight">
          Privacy Policy
        </h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Version {PRIVACY_VERSION}
        </p>

        <div className="text-foreground/90 mt-10 space-y-8 leading-relaxed">
          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              1. Overview
            </h2>
            <p>
              This Privacy Policy explains what data Nova (&ldquo;the
              Service&rdquo;), provided by Ali Technologies, collects and how we
              use it. We aim to collect only what we need to run the Service.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              2. Data we collect
            </h2>
            <ul className="list-disc space-y-1 pl-6">
              <li>
                <strong>Account data:</strong> your email address, a hashed
                password, your role, and your plan. We store the version and
                timestamp of the terms you accept.
              </li>
              <li>
                <strong>Usage data:</strong> conversations, tasks, files you
                upload, and the artifacts Nova generates, plus token/credit
                usage needed to enforce plan limits.
              </li>
              <li>
                <strong>Billing data:</strong> if you subscribe, our payment
                processor handles your payment details. We store only the
                subscription identifiers and status, never your card number.
              </li>
              <li>
                <strong>Operational data:</strong> logs and metrics used to keep
                the Service reliable and secure.
              </li>
            </ul>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              3. How we use your data
            </h2>
            <p>
              We use your data to provide and operate the Service, enforce
              usage limits, process payments, keep the Service secure, and
              communicate with you about your account. We do not sell your
              personal data.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              4. Third-party processors
            </h2>
            <p>
              To complete your requests, Nova sends the relevant content to
              third-party model providers and tools you or the Service have
              configured. Payments are processed by our payment provider. Each
              third party handles data under its own privacy policy. If you
              bring your own model-provider key, your prompts are sent to that
              provider under your account with them.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              5. Data retention
            </h2>
            <p>
              We retain your account and usage data for as long as your account
              is active or as needed to provide the Service and meet legal
              obligations. You may request deletion of your account and
              associated data.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              6. Security
            </h2>
            <p>
              Passwords are stored hashed. Sessions use HttpOnly cookies. Agent
              tasks run in isolated per-conversation sandboxes. No system is
              perfectly secure, but we take reasonable measures to protect your
              data.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              7. Your rights
            </h2>
            <p>
              Depending on your jurisdiction, you may have rights to access,
              correct, export, or delete your personal data. You can update your
              email in account settings, and contact us to exercise other
              rights.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              8. Changes
            </h2>
            <p>
              We may update this Policy. Material changes are reflected in the
              version above. Continued use after an update constitutes
              acceptance of the revised Policy.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              9. Contact
            </h2>
            <p>
              Questions about privacy? Contact{" "}
              <a
                href="mailto:alilabsx@gmail.com"
                className="text-blue-500 hover:underline"
              >
                alilabsx@gmail.com
              </a>
              .
            </p>
          </section>
        </div>

        <div className="text-muted-foreground mt-12 text-sm">
          <Link href="/terms" className="hover:underline">
            Terms of Service
          </Link>{" "}
          ·{" "}
          <Link href="/" className="hover:underline">
            Back to home
          </Link>
        </div>
      </div>
    </main>
  );
}
