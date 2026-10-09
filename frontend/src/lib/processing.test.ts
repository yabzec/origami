import { describe, expect, it } from "vitest";
import { defaultProcessing, normalizeProcessing, processingFromDocument, processingPayload } from "./processing";
import type { Document } from "./types";

describe("processing", () => {
  it("defaults everything on with the server's default languages", () => {
    expect(defaultProcessing()).toEqual({
      ocrEnabled: true,
      ocrLanguages: "",
      summaryEnabled: true,
      translationEnabled: true,
      translationLanguage: "",
    });
  });

  it("maps to the API payload; empty languages become null", () => {
    expect(processingPayload({ ...defaultProcessing(), summaryEnabled: false })).toEqual({
      ocr_enabled: true,
      ocr_languages: null,
      summary_enabled: false,
      translation_enabled: true,
      translation_language: null,
    });
  });

  it("reads a document's stored settings", () => {
    const doc = {
      ocr_enabled: false,
      ocr_languages: "deu",
      summary_enabled: true,
      translation_enabled: false,
      translation_language: "en",
    } as Document;
    expect(processingFromDocument(doc)).toEqual({
      ocrEnabled: false,
      ocrLanguages: "deu",
      summaryEnabled: true,
      translationEnabled: false,
      translationLanguage: "en",
    });
  });

  describe("normalizeProcessing", () => {
    const ocr = {
      languages: [{ code: "eng", name: "English" }, { code: "ita", name: "Italian" }],
      default: "ita+eng",
      translation_languages: [{ code: "en", name: "English" }, { code: "it", name: "Italian" }],
      translation_default: "it",
    };

    it("fills the server default when nothing is chosen", () => {
      expect(normalizeProcessing(defaultProcessing(), ocr).ocrLanguages).toBe("ita+eng");
    });

    it("drops languages that are no longer installed", () => {
      const value = { ...defaultProcessing(), ocrLanguages: "deu+eng" };
      expect(normalizeProcessing(value, ocr)).toEqual({ ...value, ocrLanguages: "eng", translationLanguage: "it" });
      expect(normalizeProcessing({ ...value, ocrLanguages: "deu" }, ocr).ocrLanguages).toBe("ita+eng");
    });

    it("returns the same object when nothing changes (no onChange loop)", () => {
      const value = { ...defaultProcessing(), ocrLanguages: "eng", translationLanguage: "en" };
      expect(normalizeProcessing(value, ocr)).toBe(value);
    });

    it("fills and repairs the translation target", () => {
      expect(normalizeProcessing(defaultProcessing(), ocr).translationLanguage).toBe("it");
      const value = { ...defaultProcessing(), ocrLanguages: "eng", translationLanguage: "de" };
      expect(normalizeProcessing(value, ocr).translationLanguage).toBe("it");
      const kept = { ...defaultProcessing(), ocrLanguages: "eng", translationLanguage: "en" };
      expect(normalizeProcessing(kept, ocr)).toBe(kept);
    });

    it("turns OCR off when no language is installed", () => {
      const none = { languages: [], default: "", translation_languages: [], translation_default: "it" };
      const value = { ...defaultProcessing(), ocrLanguages: "eng" };
      expect(normalizeProcessing(value, none)).toEqual({ ...value, ocrEnabled: false, ocrLanguages: "", translationLanguage: "it" });
      const off = { ...defaultProcessing(), ocrEnabled: false, translationLanguage: "it" };
      expect(normalizeProcessing(off, none)).toBe(off);
    });
  });
});
