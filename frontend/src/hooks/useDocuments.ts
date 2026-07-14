import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Document } from "@/lib/types";

export interface DocumentFilters {
  folderId: number | null;
  tagId: number | null;
  docType: string | null;
}

export function documentsQueryString(filters: DocumentFilters): string {
  const params = new URLSearchParams();
  if (filters.folderId !== null) params.set("folder_id", String(filters.folderId));
  if (filters.tagId !== null) params.set("tag_id", String(filters.tagId));
  if (filters.docType !== null) params.set("doc_type", filters.docType);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function documentsPollInterval(docs?: Document[]): number | false {
  return docs?.some((d) => d.status === "pending" || d.status === "processing") ? 4000 : false;
}

export function useDocuments(filters: DocumentFilters) {
  return useQuery({
    queryKey: ["documents", filters],
    queryFn: () => api.get<Document[]>(`/api/documents${documentsQueryString(filters)}`),
    refetchInterval: (query) => documentsPollInterval(query.state.data),
  });
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (form: FormData) => api.postForm<Document>("/api/documents/upload", form),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/api/documents/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}
