import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, clearToken, fileUrl, getToken, setToken } from "./api";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  clearToken();
});
afterEach(() => vi.unstubAllGlobals());

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api client", () => {
  it("attaches the bearer token", async () => {
    setToken("tok123");
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));
    await api.get("/api/auth/me");
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer tok123");
  });

  it("parses the flattened error envelope into ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, { error: { code: "duplicate_folder", message: "exists", detail: null } }),
    );
    const err = (await api.post("/api/folders", { name: "x" }).catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe("duplicate_folder");
  });

  it("returns undefined for 204", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.del("/api/tags/1")).resolves.toBeUndefined();
  });

  it("token storage round-trips", () => {
    setToken("abc");
    expect(getToken()).toBe("abc");
    clearToken();
    expect(getToken()).toBeNull();
  });

  it("fileUrl embeds the token", () => {
    setToken("tok");
    expect(fileUrl("doc-1")).toBe("/api/documents/doc-1/file?token=tok");
  });

  it("fileUrl adds preview=1 when requested", () => {
    setToken("tok");
    expect(fileUrl("doc-1", { preview: true })).toBe("/api/documents/doc-1/file?token=tok&preview=1");
  });

  it("fileUrl adds download=1 when requested", () => {
    setToken("tok");
    expect(fileUrl("doc-1", { download: true })).toBe("/api/documents/doc-1/file?token=tok&download=1");
  });
});

describe("api.upload abort", () => {
  class FakeXhr {
    upload = {};
    onabort: (() => void) | null = null;
    open() {}
    setRequestHeader() {}
    send() {}
    abort() {
      this.onabort?.();
    }
  }

  it("settles with Cancelled when the signal aborts, and immediately if already aborted", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    try {
      const { api } = await import("./api");
      const ctrl = new AbortController();
      const pending = api.upload("/x", new FormData(), undefined, ctrl.signal);
      ctrl.abort();
      await expect(pending).rejects.toMatchObject({ message: "Cancelled" });
      await expect(api.upload("/x", new FormData(), undefined, ctrl.signal)).rejects.toMatchObject({ message: "Cancelled" });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
