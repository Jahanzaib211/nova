"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import * as api from "./api";

const K = {
  lists: ["em", "lists"] as const,
  contacts: (q: string) => ["em", "contacts", q] as const,
  templates: ["em", "templates"] as const,
  campaigns: ["em", "campaigns"] as const,
  suppressions: ["em", "suppressions"] as const,
  bridges: ["em", "bridges"] as const,
  stats: (id: string) => ["em", "stats", id] as const,
  events: (id: string) => ["em", "events", id] as const,
  preflight: (id: string) => ["em", "preflight", id] as const,
};

export const useLists = () =>
  useQuery({ queryKey: K.lists, queryFn: api.listLists });
export const useContacts = (q = "") =>
  useQuery({ queryKey: K.contacts(q), queryFn: () => api.listContacts(q) });
export const useTemplates = () =>
  useQuery({ queryKey: K.templates, queryFn: api.listTemplates });
export const useCampaigns = () =>
  useQuery({
    queryKey: K.campaigns,
    queryFn: api.listCampaigns,
    refetchInterval: (q) =>
      q.state.data?.some((c) => c.status === "sending") ? 5_000 : false,
  });
export const useSuppressions = () =>
  useQuery({ queryKey: K.suppressions, queryFn: api.listSuppressions });
export const useBridges = () =>
  useQuery({ queryKey: K.bridges, queryFn: api.bridgesStatus });
export const useCampaignStats = (id: string | null, live: boolean) =>
  useQuery({
    queryKey: K.stats(id ?? ""),
    queryFn: () => api.campaignStats(id!),
    enabled: Boolean(id),
    refetchInterval: live ? 5_000 : false,
  });
export const useCampaignEvents = (id: string | null) =>
  useQuery({
    queryKey: K.events(id ?? ""),
    queryFn: () => api.campaignEvents(id!),
    enabled: Boolean(id),
  });
export const usePreflight = (id: string | null) =>
  useQuery({
    queryKey: K.preflight(id ?? ""),
    queryFn: () => api.campaignPreflight(id!),
    enabled: Boolean(id),
  });

/** Every write invalidates the whole feature: the data set is small and the
 *  cross-links (members ↔ lists ↔ campaigns) make finer keys error-prone. */
export function useEmMutations() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["em"] });
  // Named `use…` so the rules-of-hooks lint sees the fixed call order.
  const useEm = <A extends unknown[], R>(fn: (...args: A) => Promise<R>) =>
    useMutation({ mutationFn: (args: A) => fn(...args), onSuccess: done });
  const m = useEm;
  return {
    createList: m(api.createList),
    deleteList: m(api.deleteList),
    addMembers: m(api.addMembers),
    createContact: m(api.createContact),
    deleteContact: m(api.deleteContact),
    importContacts: m(api.importContacts),
    createTemplate: m(api.createTemplate),
    updateTemplate: m(api.updateTemplate),
    deleteTemplate: m(api.deleteTemplate),
    createCampaign: m(api.createCampaign),
    deleteCampaign: m(api.deleteCampaign),
    campaignAction: m(api.campaignAction),
    campaignTestSend: m(api.campaignTestSend),
    addSuppression: m(api.addSuppression),
    removeSuppression: m(api.removeSuppression),
    mailcowEnsureSender: m(api.mailcowEnsureSender),
    twentySync: m(api.twentySync),
    twentyImport: m(api.twentyImport),
  };
}
