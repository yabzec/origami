import { describe, expect, it } from "vitest";
import { pickedFromDataTransfer, pickedFromInput } from "./dropEntries";

function fileEntry(name: string) {
  return { name, isFile: true, isDirectory: false, file: (ok: (f: File) => void) => ok(new File(["x"], name)) };
}

function dirEntry(name: string, children: unknown[]) {
  return {
    name,
    isFile: false,
    isDirectory: true,
    createReader: () => {
      let done = false;
      // real readers return entries in batches and then an empty batch
      return { readEntries: (ok: (e: unknown[]) => void) => { ok(done ? [] : children); done = true; } };
    },
  };
}

describe("dropEntries", () => {
  it("walks dropped folders recursively", async () => {
    const tree = dirEntry("Bills", [fileEntry("a.pdf"), dirEntry("2025", [fileEntry("b.pdf")])]);
    const dt = { items: [{ webkitGetAsEntry: () => tree }, { webkitGetAsEntry: () => fileEntry("c.pdf") }], files: [] };
    const picked = await pickedFromDataTransfer(dt as unknown as DataTransfer);
    expect(picked.map((p) => p.relativePath)).toEqual(["Bills/a.pdf", "Bills/2025/b.pdf", "c.pdf"]);
  });

  it("falls back to plain files without entry support", async () => {
    const f = new File(["x"], "plain.pdf");
    const dt = { items: [{}], files: [f] };
    expect(await pickedFromDataTransfer(dt as unknown as DataTransfer)).toEqual([{ file: f, relativePath: "plain.pdf" }]);
  });

  it("uses webkitRelativePath from a folder input", () => {
    const f = new File(["x"], "a.pdf");
    Object.defineProperty(f, "webkitRelativePath", { value: "Bills/a.pdf" });
    expect(pickedFromInput([f])).toEqual([{ file: f, relativePath: "Bills/a.pdf" }]);
  });
});
