export interface MCPServerConfig extends Record<string, unknown> {
  enabled: boolean;
  description: string;
  type?: "stdio" | "sse" | "http";
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
}

export interface MCPConfig {
  mcp_servers: Record<string, MCPServerConfig>;
}
