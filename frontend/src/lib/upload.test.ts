import { describe, expect, it } from "vitest";
import { buildUploadForm, fileStem, uploadProcessingFields } from "./upload";
import { defaultProcessing } from "./processing";

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

  it("sends disabled summary and translation flags", () => {
    const form = buildUploadForm(new File(["x"], "a.pdf"), { summaryEnabled: false, translationEnabled: false });
    expect(form.get("summary_enabled")).toBe("false");
    expect(form.get("translation_enabled")).toBe("false");
    const defaults = buildUploadForm(new File(["x"], "a.pdf"), {});
    expect(defaults.get("summary_enabled")).toBeNull();
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

  it("appends ocr_enabled=false only when OCR is disabled", () => {
    const file = new File([new Uint8Array([1])], "a.pdf", { type: "application/pdf" });
    expect(buildUploadForm(file, {}).has("ocr_enabled")).toBe(false);
    expect(buildUploadForm(file, { ocrEnabled: true }).has("ocr_enabled")).toBe(false);
    expect(buildUploadForm(file, { ocrEnabled: false }).get("ocr_enabled")).toBe("false");
  });

  it("sends document_date when set", () => {
    const form = buildUploadForm(file, { documentDate: "2018-12-01" });
    expect(form.get("document_date")).toBe("2018-12-01");
    expect(buildUploadForm(file, {}).has("document_date")).toBe(false);
  });
});

describe("fileStem", () => {
  it("strips the extension", () => {
    expect(fileStem("bolletta marzo.pdf")).toBe("bolletta marzo");
    expect(fileStem("archive.tar.gz")).toBe("archive.tar");
    expect(fileStem("noext")).toBe("noext");
  });
});

describe("translation language in uploads", () => {
  it("sends the target only when translation is on", () => {
    const on = buildUploadForm(new File(["x"], "a.pdf"), uploadProcessingFields({ ...defaultProcessing(), translationEnabled: true, translationLanguage: "en" }));
    expect(on.get("translation_language")).toBe("en");
    expect(on.get("translation_enabled")).toBe("true");
    const off = buildUploadForm(
      new File(["x"], "a.pdf"),
      uploadProcessingFields({ ...defaultProcessing(), translationEnabled: false, translationLanguage: "en" }),
    );
    expect(off.get("translation_language")).toBeNull();
    expect(off.get("translation_enabled")).toBe("false");
  });
});
