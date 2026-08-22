export interface IGINOStatus {
  enabled: boolean;
  searxng_healthy: boolean;
  /** Health of whichever provider `web_fetch` is bound to. Named `fetch`, not
      `crawler`: it renders one URL and follows nothing, and there is now a real
      crawler in `web` to confuse it with. */
  fetch?: {
    provider: string;
    healthy: boolean;
    detail: string;
  };
  /** One entry per web capability, named by the job it does: `web_fetch` reads
      one page the agent already named, `web_fetch_many` reads several at once,
      and `web_crawl` starts at one URL and follows its links. Only the last is
      a crawler: Browserless renders a single URL, and Crawl4AI's REST API
      refuses a deep-crawl strategy from an untrusted caller, so the BFS lives
      in Nova instead. They fail independently and are reported independently.

      No `base_url`. The panel used to print `http://browserless:3000` and its
      neighbours; that publishes the compose topology to every viewer and a
      user cannot act on it. The Test buttons answer the question it stood in
      for, and the server no longer sends the field at all. */
  web?: Array<{
    tool: string;
    provider: string;
    healthy: boolean;
    detail: string;
  }>;
  /** The cross-cutting features -- cache and audit. Search and fetch are not
      repeated here: the pipeline reports them with live health, and a static
      `enabled: true` cannot go red. */
  features?: Array<{
    key: string;
    label: string;
    enabled: boolean;
    env: string;
    detail: string;
  }>;
  cache: IGINOCacheStats;
  audit: IGINOAuditStats;
  error?: string;
}

export interface IGINOCacheStats {
  size: number;
  max_size: number;
  hits: number;
  misses: number;
  hit_rate: number;
  ttl_s: number;
}

export interface IGINOAuditStats {
  total_records: number;
  /** Fetch-side counters, broken out from search so a dead crawler and a dead
      search backend are distinguishable rather than one shared error count. */
  fetches?: number;
  fetch_errors?: number;
  avg_fetch_ms?: number;
  errors: number;
  tor_usage: number;
  enabled: boolean;
  redacted: boolean;
}

export interface IGINOToggleResponse {
  enabled: boolean;
  message: string;
}

export interface IGINOResearchResult {
  query: string;
  results: IGINOResearchItem[];
  metadata: IGINOResearchMetadata;
}

export interface IGINOResearchItem {
  title: string;
  url: string;
  snippet: string;
  content: string | null;
  source: string;
  score: number;
}

export interface IGINOResearchMetadata {
  sources_searched: string[];
  sources_fetched: string[];
  sources_succeeded: number;
  privacy_mode: boolean;
  tor_used: boolean;
  duration_ms: number;
  cache_hit: boolean;
  errors: string[] | null;
}

export interface IGINOAuditRecord {
  audit_id: string;
  timestamp: string;
  thread_id: string;
  query: string;
  privacy_mode: boolean;
  tor_used: boolean;
  sources_searched: string[];
  sources_fetched: string[];
  results_returned: number;
  results_succeeded: number;
  duration_ms: number;
  cache_hit: boolean;
  error: string | null;
  compliance_tags: string[];
}

export interface IGINOCapabilities {
  enabled: boolean;
  searxng_healthy: boolean;
  circuit_states: Record<string, string>;
  cache_stats: IGINOCacheStats;
  audit_stats: IGINOAuditStats;
}
