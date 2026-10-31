import type { PageMapItem } from "nextra";
import { getPageMap } from "nextra/page-map";
import { Layout } from "nextra-theme-docs";

import { Footer } from "@/components/landing/footer";
import { Header } from "@/components/landing/header";
import { getLocaleByLang } from "@/core/i18n/locale";
import "nextra-theme-docs/style.css";

const i18n = [
  { locale: "en", name: "English" },
  { locale: "zh", name: "中文" },
];

/**
 * The top-level sections that are actually documentation.
 *
 * `getPageMap` returns every route in the app as well as the MDX content:
 * `/workspace`, `/blog`, `/login`, `/saas` and the like. Prefixing those with
 * `/docs` produced a sidebar full of links to pages that do not exist. The
 * page map is filtered to these roots before the prefix is applied.
 */
const DOCS_ROOTS = new Set([
  "",
  "introduction",
  "harness",
  "application",
  "tutorials",
  "reference",
]);

function filterDocsPageMap(items: PageMapItem[]): PageMapItem[] {
  return items.filter((item) => {
    if (!("route" in item) || typeof item.route !== "string") return false;
    const root = item.route.replace(/^\//, "").split("/")[0] ?? "";
    return DOCS_ROOTS.has(root);
  });
}

function formatPageRoute(base: string, items: PageMapItem[]): PageMapItem[] {
  // `getPageMap` returns a cached object graph. Mutating `route` in place
  // corrupted that cache: Nextra's own route→file lookup (used by `importPage`)
  // then saw `/en/docs/introduction` where it expected `/introduction`, and
  // every docs subpage 404'd. Clone each item instead.
  return items.map((item) => {
    const next = { ...item };
    if ("route" in next && !next.route.startsWith(base)) {
      next.route = `${base}${next.route}`;
    }
    if ("children" in next && next.children) {
      next.children = formatPageRoute(base, next.children);
    }
    return next;
  });
}

export default async function DocLayout({ children, params }) {
  const { lang } = await params;
  const locale = getLocaleByLang(lang);
  const pages = await getPageMap(`/${lang}`);
  const pageMap = formatPageRoute(`/${lang}/docs`, filterDocsPageMap(pages));

  return (
    <Layout
      navbar={
        <Header
          className="sticky max-w-full px-10"
          homeURL="/"
          locale={locale}
          tone="light"
        />
      }
      pageMap={pageMap}
      docsRepositoryBase="https://github.com/Jahanzaib211/nova/tree/main/frontend/src/content"
      footer={<Footer className="mt-0" />}
      i18n={i18n}
      // ... Your additional layout options
    >
      {children}
    </Layout>
  );
}
