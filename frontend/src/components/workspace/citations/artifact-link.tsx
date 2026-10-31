import type { AnchorHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

import { CitationLink } from "./citation-link";

function isExternalUrl(href: string | undefined): boolean {
  return !!href && /^https?:\/\//.test(href);
}

/** Link renderer for artifact markdown: citation: prefix → CitationLink, otherwise underlined text. */
export function ArtifactLink(props: AnchorHTMLAttributes<HTMLAnchorElement>) {
  if (typeof props.children === "string") {
    const match = /^citation:(.+)$/.exec(props.children);
    if (match) {
      const [, text] = match;
      return <CitationLink {...props}>{text}</CitationLink>;
    }
  }
  const { className, target, rel, ...rest } = props;
  const external = isExternalUrl(props.href);
  return (
    <a
      {...rest}
      className={cn(
        // `wrap-anywhere` is not decoration. Streamdown's own <a> ships with it,
        // and overriding the component silently dropped it -- so a long bare
        // URL (auto-linked, no spaces to break on) overflowed its container
        // instead of wrapping. Terminal output already gets this right via
        // `break-all`; this is the same problem in the chat and reader.
        "text-primary decoration-primary/30 hover:decoration-primary/60 wrap-anywhere underline underline-offset-2 transition-colors",
        className,
      )}
      target={target ?? (external ? "_blank" : undefined)}
      rel={rel ?? (external ? "noopener noreferrer" : undefined)}
    />
  );
}
