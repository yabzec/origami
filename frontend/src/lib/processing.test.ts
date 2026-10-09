import { describe, expect, it } from "vitest";
import { defaultProcessing, processingFromDocument, processingPayload } from "./processing";
import type { Document } from "./types";

describe("processing", () => {
  it("defaults everything on with the server's default languages", () => {
    expect(defaultProcessing()).toEqual({
      ocrEnabled: true,
      ocrLanguages: "",
      summaryEnabled: true,
      translationEnabled: true,
    });
  });

  it("maps to the API payload; empty languages become null", () => {
    expect(processingPayload({ ...defaultProcessing(), summaryEnabled: false })).toEqual({
      ocr_enabled: true,
      ocr_languages: null,
      summary_enabled: false,
      translation_enabled: true,
    });
  });

  it("reads a document's stored settings", () => {
    const doc = {
      ocr_enabled: false,
      ocr_languages: "deu",
      summary_enabled: true,
      translation_enabled: false,
    } as Document;
    expect(processingFromDocument(doc)).toEqual({
      ocrEnabled: false,
      ocrLanguages: "deu",
      summaryEnabled: true,
      translationEnabled: false,
    });
  });
});
