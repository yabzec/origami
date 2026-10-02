import { describe, expect, it } from "vitest";
import { iconKind } from "./docIcons";
import type { DocType } from "./types";

const doc = (doc_type: DocType, original_filename: string | null = null) => ({ doc_type, original_filename });

describe("iconKind", () => {
  it("uses the PDF icon for PDFs and scans", () => {
    expect(iconKind(doc("pdf", "a.pdf"))).toBe("pdf");
    expect(iconKind(doc("scan"))).toBe("pdf");
  });

  it("uses the Word icon for office text files, case-insensitively", () => {
    for (const name of ["a.doc", "a.docx", "a.odt", "a.rtf", "Contratto.DOCX"]) {
      expect(iconKind(doc("text", name))).toBe("word");
    }
  });

  it("uses the text icon for other text files", () => {
    expect(iconKind(doc("text", "note.md"))).toBe("text");
    expect(iconKind(doc("text", "readme.txt"))).toBe("text");
    expect(iconKind(doc("text", null))).toBe("text");
  });

  it("maps images and videos", () => {
    expect(iconKind(doc("image", "a.jpg"))).toBe("image");
    expect(iconKind(doc("video", "a.mp4"))).toBe("video");
  });
});
