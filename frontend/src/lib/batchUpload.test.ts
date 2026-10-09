import { describe, expect, it, vi } from "vitest";
import { batchCounts, localIsoDate, planBatch, runBatch, type BatchDeps, type ItemState } from "./batchUpload";
import { defaultProcessing } from "./processing";

const file = (name: string, lastModified = new Date(2025, 2, 4, 10).getTime()) =>
  new File(["x"], name, { lastModified });

describe("planBatch", () => {
  it("derives title, date and folder segments", () => {
    const { items, skipped } = planBatch([{ file: file("Invoice ACME.pdf"), relativePath: "Bills/2025/Invoice ACME.pdf" }]);
    expect(skipped).toEqual([]);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      title: "Invoice ACME",
      documentDate: "2025-03-04",
      folderSegments: ["Bills", "2025"],
      relativePath: "Bills/2025/Invoice ACME.pdf",
    });
  });

  it("skips unsupported and hidden files with a reason", () => {
    const { items, skipped } = planBatch([
      { file: file("a.exe"), relativePath: "Docs/a.exe" },
      { file: file(".DS_Store"), relativePath: "Docs/.DS_Store" },
      { file: file("Thumbs.db"), relativePath: "Docs/Thumbs.db" },
      { file: file("b.pdf"), relativePath: "Docs/.git/b.pdf" },
      { file: file("ok.JPG"), relativePath: "ok.JPG" },
    ]);
    expect(items.map((i) => i.relativePath)).toEqual(["ok.JPG"]);
    expect(items[0].folderSegments).toEqual([]);
    expect(skipped).toEqual([
      { relativePath: "Docs/a.exe", reason: "unsupported type .exe" },
      { relativePath: "Docs/.DS_Store", reason: "hidden or system file" },
      { relativePath: "Docs/Thumbs.db", reason: "hidden or system file" },
      { relativePath: "Docs/.git/b.pdf", reason: "hidden or system file" },
    ]);
  });

  it("formats the local date", () => {
    expect(localIsoDate(new Date(2024, 11, 31, 23, 59).getTime())).toBe("2024-12-31");
  });
});

function fakeDeps(overrides: Partial<BatchDeps> = {}) {
  const ensurePath = vi.fn(async (_parent: number | null, segments: string[]) => segments.length * 10);
  const uploads: FormData[] = [];
  const upload = vi.fn(async (form: FormData, onProgress: (p: number) => void) => {
    onProgress(50);
    uploads.push(form);
  });
  return { deps: { ensurePath, upload, ...overrides }, ensurePath, upload, uploads };
}

const options = { folderId: 1, tagIds: [7], processing: { ...defaultProcessing(), translationLanguage: "en" } };

describe("runBatch", () => {
  it("resolves each folder once and uploads every file", async () => {
    const { items } = planBatch([
      { file: file("a.pdf"), relativePath: "A/B/a.pdf" },
      { file: file("b.pdf"), relativePath: "A/B/b.pdf" },
      { file: file("c.pdf"), relativePath: "c.pdf" },
    ]);
    const { deps, ensurePath, uploads } = fakeDeps();
    const states: Record<string, ItemState> = {};
    await runBatch(items, options, deps, (k, s) => (states[k] = s));
    expect(ensurePath).toHaveBeenCalledTimes(1);
    expect(ensurePath).toHaveBeenCalledWith(1, ["A", "B"]);
    expect(uploads.map((f) => f.get("folder_id")).sort()).toEqual(["1", "20", "20"]);
    expect(uploads[0].get("tag_ids")).toBe("7");
    expect(uploads[0].get("translation_language")).toBe("en");
    expect(Object.values(states).every((s) => s.status === "done")).toBe(true);
  });

  it("stops after an abort: in-flight upload is Cancelled, later items never start", async () => {
    const ctrl = new AbortController();
    const upload = vi.fn(
      (_form: FormData, _p: (n: number) => void, signal?: AbortSignal) =>
        new Promise<unknown>((_, reject) => {
          signal?.addEventListener("abort", () => reject(new Error("aborted")));
        }),
    );
    const { items } = planBatch(Array.from({ length: 4 }, (_, i) => ({ file: file(`${i}.pdf`), relativePath: `${i}.pdf` })));
    const states: Record<string, ItemState> = {};
    const done = runBatch(items, options, fakeDeps({ upload }).deps, (k, s) => (states[k] = s), 1, ctrl.signal);
    await vi.waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    ctrl.abort();
    await done;
    expect(upload).toHaveBeenCalledTimes(1);
    expect(states[items[0].key]).toEqual({ status: "failed", message: "Cancelled" });
    expect(states[items[1].key]).toBeUndefined();
  });

  it("runs at most three uploads at once", async () => {
    let running = 0;
    let peak = 0;
    const upload = vi.fn(async () => {
      running++;
      peak = Math.max(peak, running);
      await new Promise((r) => setTimeout(r, 5));
      running--;
    });
    const { items } = planBatch(Array.from({ length: 7 }, (_, i) => ({ file: file(`${i}.pdf`), relativePath: `${i}.pdf` })));
    await runBatch(items, options, fakeDeps({ upload }).deps, () => {});
    expect(upload).toHaveBeenCalledTimes(7);
    expect(peak).toBe(3);
  });

  it("fails only the files of a folder that could not be created, and retry resolves again", async () => {
    const { items } = planBatch([
      { file: file("a.pdf"), relativePath: "Bad/a.pdf" },
      { file: file("b.pdf"), relativePath: "b.pdf" },
    ]);
    let calls = 0;
    const ensurePath = vi.fn(async () => {
      calls++;
      if (calls === 1) throw new Error("folder refused");
      return 5;
    });
    const { deps, uploads } = fakeDeps({ ensurePath });
    const states: Record<string, ItemState> = {};
    await runBatch(items, options, deps, (k, s) => (states[k] = s));
    expect(states[items[0].key]).toEqual({ status: "failed", message: "folder refused" });
    expect(states[items[1].key]).toEqual({ status: "done" });
    expect(batchCounts(items, states)).toEqual({ total: 2, done: 1, failed: 1 });

    await runBatch([items[0]], options, deps, (k, s) => (states[k] = s));
    expect(states[items[0].key]).toEqual({ status: "done" });
    expect(uploads.at(-1)?.get("folder_id")).toBe("5");
  });

  it("marks a failed upload and continues", async () => {
    const upload = vi.fn(async (form: FormData) => {
      if ((form.get("file") as File).name === "a.pdf") throw new Error("Unsupported");
    });
    const { items } = planBatch([
      { file: file("a.pdf"), relativePath: "a.pdf" },
      { file: file("b.pdf"), relativePath: "b.pdf" },
    ]);
    const states: Record<string, ItemState> = {};
    await runBatch(items, options, fakeDeps({ upload }).deps, (k, s) => (states[k] = s));
    expect(states[items[0].key]).toEqual({ status: "failed", message: "Unsupported" });
    expect(states[items[1].key]).toEqual({ status: "done" });
  });
});
