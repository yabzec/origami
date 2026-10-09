const TOKEN_KEY = "origami_token";

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const setToken = (token: string): void => localStorage.setItem(TOKEN_KEY, token);
export const clearToken = (): void => localStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  status: number;
  code: string;
  detail?: unknown;

  constructor(status: number, code: string, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  form?: FormData,
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const resp = await fetch(path, {
    method,
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (resp.status === 204) return undefined as T;
  const data = await resp.json().catch(() => null);
  if (!resp.ok) {
    const err = (data as { error?: { code?: string; message?: string; detail?: unknown } })?.error;
    throw new ApiError(resp.status, err?.code ?? "unknown_error", err?.message ?? resp.statusText, err?.detail);
  }
  return data as T;
}

/** multipart POST with upload progress (fetch has no upload progress events). */
function uploadForm<T>(
  path: string,
  form: FormData,
  onProgress?: (percent: number) => void,
  signal?: AbortSignal,
): Promise<T> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new ApiError(0, "aborted", "Cancelled"));
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    const token = getToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data as T);
        return;
      }
      const err = (data as { error?: { code?: string; message?: string; detail?: unknown } })?.error;
      reject(new ApiError(xhr.status, err?.code ?? "unknown_error", err?.message ?? (xhr.statusText || "Upload failed"), err?.detail));
    };
    xhr.onerror = () => reject(new ApiError(0, "network_error", "Network error"));
    xhr.onabort = () => reject(new ApiError(0, "aborted", "Cancelled"));
    xhr.ontimeout = () => reject(new ApiError(0, "timeout", "Upload timed out"));
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(form);
  });
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<void>("DELETE", path),
  upload: uploadForm,
  postForm: <T>(path: string, form: FormData) => request<T>("POST", path, undefined, form),
};

export const fileUrl = (documentId: string, opts: { download?: boolean; preview?: boolean } = {}): string =>
  `/api/documents/${documentId}/file?token=${getToken() ?? ""}` +
  `${opts.download ? "&download=1" : ""}${opts.preview ? "&preview=1" : ""}`;
