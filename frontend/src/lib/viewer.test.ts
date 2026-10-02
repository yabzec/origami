import { describe, expect, it } from "vitest";
import { viewerKind } from "./viewer";
import type { DocType } from "./types";

const doc = (doc_type: DocType, preview_path: string | null = null) => ({ doc_type, preview_path });

describe("viewerKind", () => {
  it("maps every doc type", () => {
    expect(viewerKind(doc("scan"))).toBe("pdf");
    expect(viewerKind(doc("pdf"))).toBe("pdf");
    expect(viewerKind(doc("image"))).toBe("image");
    expect(viewerKind(doc("video"))).toBe("video");
    expect(viewerKind(doc("text"))).toBe("text");
  });

  it("shows a text document with a preview as PDF", () => {
    expect(viewerKind(doc("text", "files/x.preview.pdf"))).toBe("pdf");
  });
});
