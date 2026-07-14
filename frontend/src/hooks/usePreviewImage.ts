import { useEffect, useState } from "react";
import { getToken } from "@/lib/api";

export function usePreviewImage(pageId: number): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    fetch(`/api/scan/pages/${pageId}/preview`, {
      headers: { Authorization: `Bearer ${getToken() ?? ""}` },
    })
      .then((resp) => (resp.ok ? resp.blob() : Promise.reject(new Error("preview failed"))))
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setUrl(objectUrl);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [pageId]);

  return url;
}
