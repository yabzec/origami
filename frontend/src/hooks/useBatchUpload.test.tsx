import { describe, expect, it, vi, beforeEach } from "vitest";
import type { ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { defaultProcessing } from "@/lib/processing";
import type { BatchItem } from "@/lib/batchUpload";

const signals: AbortSignal[] = [];

vi.mock("@/lib/api", () => ({
  api: {
    post: vi.fn(),
    upload: vi.fn(
      (_path: string, _form: FormData, _onProgress: unknown, signal?: AbortSignal) =>
        new Promise((_resolve, reject) => {
          if (signal) signals.push(signal);
          signal?.addEventListener("abort", () => reject(new Error("Cancelled")), { once: true });
        }),
    ),
  },
}));

import { useBatchUpload } from "./useBatchUpload";

const item: BatchItem = {
  key: "a.pdf",
  file: new File(["x"], "a.pdf"),
  relativePath: "a.pdf",
  title: "a",
  documentDate: "2026-10-09",
  folderSegments: [],
};
const options = { folderId: null, tagIds: [], processing: defaultProcessing() };

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

describe("useBatchUpload", () => {
  beforeEach(() => {
    signals.length = 0;
  });

  it("cancel aborts the upload in flight", async () => {
    const { result } = renderHook(() => useBatchUpload(), { wrapper });
    let done!: Promise<void>;
    act(() => {
      done = result.current.run([item], options);
    });
    await waitFor(() => expect(signals).toHaveLength(1));
    expect(signals[0].aborted).toBe(false);
    act(() => result.current.cancel());
    expect(signals[0].aborted).toBe(true);
    await act(() => done);
  });

  it("unmount aborts the upload in flight", async () => {
    const { result, unmount } = renderHook(() => useBatchUpload(), { wrapper });
    act(() => {
      void result.current.run([item], options);
    });
    await waitFor(() => expect(signals).toHaveLength(1));
    unmount();
    expect(signals[0].aborted).toBe(true);
  });
});
