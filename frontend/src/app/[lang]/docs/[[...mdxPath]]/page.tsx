import { notFound } from "next/navigation";
import { generateStaticParamsFor, importPage } from "nextra/pages";

import { useMDXComponents as getMDXComponents } from "../../../../mdx-components";

export const generateStaticParams = generateStaticParamsFor("mdxPath");

/**
 * Load one MDX page, or 404.
 *
 * `importPage` resolves a page by building a module specifier from the URL
 * segments, so a path with no matching file throws `MODULE_NOT_FOUND` rather
 * than returning null. Unguarded, that surfaced as a thrown server error for
 * any docs URL that does not exist — e.g. `/en/docs/workspace` logged
 * `Cannot find module 'private-next-content-dir/en/undefined'` out of both
 * `generateMetadata` and `Page`, twice per request, instead of rendering the
 * normal not-found page.
 *
 * Only a missing module is treated as a 404; anything else (a broken MDX file,
 * a failing import inside a page) is rethrown, so real errors stay visible
 * instead of being flattened into "page not found".
 */
async function loadPage(mdxPath: string[] | undefined, lang: string) {
  try {
    return await importPage(mdxPath, lang);
  } catch (error) {
    const code = (error as { code?: string } | null)?.code;
    if (code === "MODULE_NOT_FOUND") notFound();
    throw error;
  }
}

export async function generateMetadata(props) {
  const params = await props.params;
  const { metadata } = await loadPage(params.mdxPath, params.lang);
  return metadata;
}

// eslint-disable-next-line @typescript-eslint/unbound-method
const Wrapper = getMDXComponents().wrapper;

export default async function Page(props) {
  const params = await props.params;
  const {
    default: MDXContent,
    toc,
    metadata,
    sourceCode,
  } = await loadPage(params.mdxPath, params.lang);
  return (
    <Wrapper toc={toc} metadata={metadata} sourceCode={sourceCode}>
      <MDXContent {...props} params={params} />
    </Wrapper>
  );
}
