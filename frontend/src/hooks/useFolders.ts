import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Folder } from "@/lib/types";

export function useFolders() {
  return useQuery({ queryKey: ["folders"], queryFn: () => api.get<Folder[]>("/api/folders") });
}

function useInvalidateFolders() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["folders"] });
}

export function useCreateFolder() {
  const invalidate = useInvalidateFolders();
  return useMutation({
    mutationFn: (body: { name: string; parent_id: number | null }) => api.post<Folder>("/api/folders", body),
    onSuccess: invalidate,
  });
}

export function useRenameFolder() {
  const invalidate = useInvalidateFolders();
  return useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => api.patch<Folder>(`/api/folders/${id}`, { name }),
    onSuccess: invalidate,
  });
}
