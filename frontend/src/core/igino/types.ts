export interface IGINOStatus {
  enabled: boolean;
  tor_enabled: boolean;
  tor_available: boolean;
  searxng_healthy: boolean;
  base_url: string;
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
  tor_enabled: boolean;
  tor_available: boolean;
  searxng_healthy: boolean;
  circuit_states: Record<string, string>;
  cache_stats: IGINOCacheStats;
  audit_stats: IGINOAuditStats;
}
