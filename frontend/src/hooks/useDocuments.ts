import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DEFAULT_SORT, type DocumentSort } from "@/lib/sorting";
import type { BulkResult, Document } from "@/lib/types";

export interface DocumentFilters {
  folder: number | "root" | null; // null = every folder
  tagId: number | null;
  docType: string | null;
  dateFrom?: string | null;
  dateTo?: string | null;
  sort?: DocumentSort;
}

export function documentsQueryString(filters: DocumentFilters): string {
  const params = new URLSearchParams();
  if (filters.folder !== null) params.set("folder_id", String(filters.folder));
  if (filters.tagId !== null) params.set("tag_id", String(filters.tagId));
  if (filters.docType !== null) params.set("doc_type", filters.docType);
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  if (filters.sort && filters.sort !== DEFAULT_SORT) params.set("sort", filters.sort);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function documentsPollInterval(docs?: Document[]): number | false {
  return docs?.some((d) => d.status === "pending" || d.status === "processing") ? 4000 : false;
}

export function useDocuments(filters: DocumentFilters, enabled = true) {
  return useQuery({
    queryKey: ["documents", filters],
    queryFn: () => api.get<Document[]>(`/api/documents${documentsQueryString(filters)}`),
    enabled,
    refetchInterval: (query) => documentsPollInterval(query.state.data),
  });
}

export function useUploadDocument() {
  const invalidate = useInvalidateListing();
  return useMutation({
    mutationFn: (form: FormData) => api.postForm<Document>("/api/documents/upload", form),
    onSuccess: invalidate,
  });
}

function useInvalidateListing() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["documents"] });
    qc.invalidateQueries({ queryKey: ["folders"] }); // folder tiles show document counts
  };
}

export function useDeleteDocument() {
  const invalidate = useInvalidateListing();
  return useMutation({
    mutationFn: (id: string) => api.del(`/api/documents/${id}`),
    onSuccess: invalidate,
  });
}

export function useBulkMove() {
  const invalidate = useInvalidateListing();
  return useMutation({
    mutationFn: (body: { ids: string[]; folder_id: number | null }) =>
      api.post<BulkResult>("/api/documents/bulk/move", body),
    onSuccess: invalidate,
  });
}

export function useBulkDelete() {
  const invalidate = useInvalidateListing();
  return useMutation({
    mutationFn: (ids: string[]) => api.post<BulkResult>("/api/documents/bulk/delete", { ids }),
    onSuccess: invalidate,
  });
}
