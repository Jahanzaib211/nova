/**
 * React hooks for file uploads
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import {
  deleteUploadedFile,
  listUploadedFiles,
  loadUploadLimits,
  uploadFiles,
  type UploadedFileInfo,
  type UploadLimits,
  type UploadResponse,
} from "./api";

/**
 * Hook to upload files
 */
export function useUploadFiles(threadId: string) {
  const queryClient = useQueryClient();

  return useMutation<UploadResponse, Error, File[]>({
    mutationFn: (files: File[]) => uploadFiles(threadId, files),
    onSuccess: () => {
      // Invalidate the uploaded files list
      void queryClient.invalidateQueries({
        queryKey: ["uploads", "list", threadId],
      });
    },
  });
}

/**
 * Hook to list uploaded files
 */
/** Unwired: no attachment list UI. GET /api/uploads is live. */
export function useUploadedFiles(threadId: string) {
  return useQuery({
    queryKey: ["uploads", "list", threadId],
    queryFn: () => listUploadedFiles(threadId),
    enabled: !!threadId,
  });
}

/**
 * Hook to fetch upload limits (per-file / total size, file count).
 */
export function useUploadLimits(threadId: string) {
  return useQuery({
    queryKey: ["uploads", "limits", threadId],
    queryFn: () => loadUploadLimits(threadId),
    enabled: !!threadId,
    staleTime: 5 * 60 * 1000,
  });
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "-";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(0)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

export function summarizeUploadLimits(
  limits: UploadLimits | undefined,
): string {
  if (!limits) return "";
  const parts: string[] = [];
  if (limits.max_files > 0) parts.push(`${limits.max_files} files max`);
  if (limits.max_file_size > 0)
    parts.push(`${formatBytes(limits.max_file_size)} per file`);
  if (limits.max_total_size > 0)
    parts.push(`${formatBytes(limits.max_total_size)} total`);
  return parts.join(" · ");
}

/**
 * Hook to delete an uploaded file
 */
/** Unwired: no attachment list UI, so nothing offers deletion. Route is live. */
export function useDeleteUploadedFile(threadId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (filename: string) => deleteUploadedFile(threadId, filename),
    onSuccess: () => {
      // Invalidate the uploaded files list
      void queryClient.invalidateQueries({
        queryKey: ["uploads", "list", threadId],
      });
    },
  });
}

/**
 * Hook to handle file uploads in submit flow
 * Returns a function that uploads files and returns their info
 */
/**
 * Unwired: this is the attach-a-file-to-your-message path, complete and
 * never called -- the composer has no attachment control. Kept because the
 * API layer and types below it exist only to serve this; deleting it would
 * strand them rather than remove dead weight.
 */
export function useUploadFilesOnSubmit(threadId: string) {
  const uploadMutation = useUploadFiles(threadId);

  return useCallback(
    async (files: File[]): Promise<UploadedFileInfo[]> => {
      if (files.length === 0) {
        return [];
      }

      const result = await uploadMutation.mutateAsync(files);
      return result.files;
    },
    [uploadMutation],
  );
}
