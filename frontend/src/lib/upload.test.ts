import { describe, expect, it } from "vitest";
import { buildUploadForm, fileStem } from "./upload";

describe("buildUploadForm", () => {
  const file = new File([new Uint8Array([1])], "bolletta marzo.pdf", { type: "application/pdf" });

  it("always includes the file, omits unset fields", () => {
    const form = buildUploadForm(file, {});
    expect(form.get("file")).toBe(file);
    expect(form.has("title")).toBe(false);
    expect(form.has("folder_id")).toBe(false);
    expect(form.has("tag_ids")).toBe(false);
    expect(form.has("ocr_languages")).toBe(false);
  });

  it("serializes all fields", () => {
    const form = buildUploadForm(file, {
      title: "Bolletta",
      folderId: 7,
      tagIds: [1, 3],
      ocrLanguages: "ita",
    });
    expect(form.get("title")).toBe("Bolletta");
    expect(form.get("folder_id")).toBe("7");
    expect(form.get("tag_ids")).toBe("1,3");
    expect(form.get("ocr_languages")).toBe("ita");
  });

  it("omits empty tag list and null folder", () => {
    const form = buildUploadForm(file, { folderId: null, tagIds: [] });
    expect(form.has("folder_id")).toBe(false);
    expect(form.has("tag_ids")).toBe(false);
  });
});

describe("fileStem", () => {
  it("strips the extension", () => {
    expect(fileStem("bolletta marzo.pdf")).toBe("bolletta marzo");
    expect(fileStem("archive.tar.gz")).toBe("archive.tar");
    expect(fileStem("noext")).toBe("noext");
  });
});
