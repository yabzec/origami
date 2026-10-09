import { keepInstalled } from "./ocrLanguages";
import type { Document, OcrLanguagesResponse } from "./types";

export interface ProcessingValues {
  ocrEnabled: boolean;
  ocrLanguages: string; // "" = not chosen yet: use the server default
  summaryEnabled: boolean;
  translationEnabled: boolean;
}

export function defaultProcessing(): ProcessingValues {
  return { ocrEnabled: true, ocrLanguages: "", summaryEnabled: true, translationEnabled: true };
}

export function processingFromDocument(doc: Document): ProcessingValues {
  return {
    ocrEnabled: doc.ocr_enabled,
    ocrLanguages: doc.ocr_languages,
    summaryEnabled: doc.summary_enabled,
    translationEnabled: doc.translation_enabled,
  };
}

export function processingPayload(v: ProcessingValues) {
  return {
    ocr_enabled: v.ocrEnabled,
    ocr_languages: v.ocrLanguages || null,
    summary_enabled: v.summaryEnabled,
    translation_enabled: v.translationEnabled,
  };
}

/** Fit the values to the installed OCR languages; the same object when nothing changes. */
export function normalizeProcessing(v: ProcessingValues, ocr: OcrLanguagesResponse): ProcessingValues {
  const codes = ocr.languages.map((l) => l.code);
  const ocrLanguages = keepInstalled(v.ocrLanguages, codes, ocr.default);
  return ocrLanguages === v.ocrLanguages ? v : { ...v, ocrLanguages };
}
