import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { OcrLanguagesResponse } from "@/lib/types";

export function useOcrLanguages() {
  return useQuery({
    queryKey: ["ocr-languages"],
    queryFn: () => api.get<OcrLanguagesResponse>("/api/ocr/languages"),
    staleTime: 5 * 60_000, // the server re-reads Tesseract every 5 minutes
  });
}
