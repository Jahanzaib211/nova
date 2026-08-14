import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  loadMCPConfig,
  MCPConfigRequestError,
  resetMCPCache,
  updateMCPConfig,
} from "./api";
import type { MCPServerConfig } from "./types";

export function useMCPConfig() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["mcpConfig"],
    queryFn: () => loadMCPConfig(),
    retry: (count, error) =>
      !(error instanceof MCPConfigRequestError) && count < 3,
  });
  return { config: data, isLoading, error };
}

export function useEnableMCPServer() {
  const queryClient = useQueryClient();
  const { config } = useMCPConfig();
  return useMutation({
    mutationFn: async ({
      serverName,
      enabled,
    }: {
      serverName: string;
      enabled: boolean;
    }) => {
      if (!config) {
        throw new Error("MCP config not found");
      }
      if (!config.mcp_servers[serverName]) {
        throw new Error(`MCP server ${serverName} not found`);
      }
      await updateMCPConfig({
        mcp_servers: {
          ...config.mcp_servers,
          [serverName]: {
            ...config.mcp_servers[serverName],
            enabled,
          },
        },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
  });
}

export function useCreateMCPServer() {
  const queryClient = useQueryClient();
  const { config } = useMCPConfig();
  return useMutation({
    mutationFn: async ({
      serverName,
      server,
    }: {
      serverName: string;
      server: MCPServerConfig;
    }) => {
      if (!config) {
        throw new Error("MCP config not found");
      }
      if (config.mcp_servers[serverName]) {
        throw new Error(`MCP server ${serverName} already exists`);
      }
      await updateMCPConfig({
        mcp_servers: { ...config.mcp_servers, [serverName]: server },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
  });
}

export function useUpdateMCPServer() {
  const queryClient = useQueryClient();
  const { config } = useMCPConfig();
  return useMutation({
    mutationFn: async ({
      serverName,
      server,
    }: {
      serverName: string;
      server: MCPServerConfig;
    }) => {
      if (!config) {
        throw new Error("MCP config not found");
      }
      if (!config.mcp_servers[serverName]) {
        throw new Error(`MCP server ${serverName} not found`);
      }
      await updateMCPConfig({
        mcp_servers: { ...config.mcp_servers, [serverName]: server },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
  });
}

export function useDeleteMCPServer() {
  const queryClient = useQueryClient();
  const { config } = useMCPConfig();
  return useMutation({
    mutationFn: async (serverName: string) => {
      if (!config) {
        throw new Error("MCP config not found");
      }
      const remaining = { ...config.mcp_servers };
      delete remaining[serverName];
      await updateMCPConfig({ mcp_servers: remaining });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
  });
}

export function useResetMCPCache() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => resetMCPCache(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
  });
}
