import { type Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Terms of Service — Nova",
  description:
    "The terms that govern your use of Nova, the computer agent by Ali Technologies.",
};

// Keep in sync with backend app/gateway/legal.py TOS_VERSION.
const TOS_VERSION = "2026-07-15";

export default function TermsPage() {
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
          Terms of Service
        </h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Version {TOS_VERSION}
        </p>

        <div className="text-foreground/90 mt-10 space-y-8 leading-relaxed">
          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              1. Acceptance of these terms
            </h2>
            <p>
              These Terms of Service (&ldquo;Terms&rdquo;) govern your access to
              and use of Nova (&ldquo;the Service&rdquo;), a computer agent
              provided by Ali Technologies (&ldquo;we&rdquo;, &ldquo;us&rdquo;).
              By creating an account or using the Service you agree to these
              Terms. If you do not agree, do not use the Service.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              2. The Service
            </h2>
            <p>
              Nova is an AI agent that researches, writes code, and executes
              tasks on your behalf inside isolated sandboxes. Nova acts on the
              instructions you give it and may call third-party model providers
              and tools to complete your requests. You are responsible for the
              instructions you provide and for reviewing Nova&rsquo;s output
              before relying on it.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              3. Your account
            </h2>
            <p>
              You are responsible for maintaining the confidentiality of your
              account credentials and for all activity under your account. You
              must provide an accurate email address and promptly update it if
              it changes. You must be legally capable of entering into these
              Terms in your jurisdiction.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              4. Acceptable use
            </h2>
            <p>You agree not to use the Service to:</p>
            <ul className="list-disc space-y-1 pl-6">
              <li>violate any applicable law or the rights of others;</li>
              <li>
                generate or distribute malware, conduct unauthorized security
                testing, or attack any system you do not own or have permission
                to test;
              </li>
              <li>
                process content you are not authorized to process, or attempt to
                de-anonymize, harass, or harm any individual;
              </li>
              <li>
                circumvent usage limits, rate limits, or the credit system, or
                resell the Service without authorization.
              </li>
            </ul>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              5. Credits, plans, and billing
            </h2>
            <p>
              The Service offers a free tier with a daily usage allowance
              measured in credits, and paid plans (&ldquo;Nova Plus&rdquo;) with
              higher allowances and additional features. Paid subscriptions are
              billed through our payment processor. Daily free allowances reset
              each day and do not roll over; purchased top-up credits roll over
              until used. Prices and allowances may change with reasonable
              notice. You may bring your own model-provider API key, in which
              case usage against that key is governed by that provider&rsquo;s
              terms.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              6. Third-party services
            </h2>
            <p>
              Nova relies on third-party model providers, search tools, and
              infrastructure. Your use of those services through Nova is also
              subject to their respective terms. We are not responsible for the
              availability, accuracy, or content of third-party services.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              7. Intellectual property
            </h2>
            <p>
              You retain ownership of the content you provide and, to the extent
              permitted by law and third-party model terms, the output Nova
              generates for you. Nova&rsquo;s open-source foundation is licensed
              separately under its published license.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              8. Disclaimers and limitation of liability
            </h2>
            <p>
              The Service is provided &ldquo;as is&rdquo; without warranties of
              any kind. AI output may be inaccurate or incomplete; you are
              responsible for reviewing it. To the maximum extent permitted by
              law, Ali Technologies is not liable for any indirect, incidental,
              or consequential damages arising from your use of the Service.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              9. Termination
            </h2>
            <p>
              You may stop using the Service at any time. We may suspend or
              terminate access for violations of these Terms or to comply with
              the law. Provisions that by their nature should survive
              termination will survive.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              10. Changes to these terms
            </h2>
            <p>
              We may update these Terms. When we do, we will update the version
              above and ask you to re-accept before you continue using the
              Service. Continued use after acceptance constitutes agreement to
              the updated Terms.
            </p>
          </section>

          <section className="space-y-3">
            <h2 className="text-foreground text-xl font-semibold">
              11. Contact
            </h2>
            <p>
              Questions about these Terms? Contact{" "}
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
          <Link href="/privacy" className="hover:underline">
            Privacy Policy
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
