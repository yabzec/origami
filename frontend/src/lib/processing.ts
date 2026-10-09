import type { Document } from "./types";

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
