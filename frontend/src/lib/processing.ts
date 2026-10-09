import { keepInstalled } from "./ocrLanguages";
import type { Document, OcrLanguagesResponse } from "./types";

export interface ProcessingValues {
  ocrEnabled: boolean;
  ocrLanguages: string; // "" = not chosen yet: use the server default
  summaryEnabled: boolean;
  translationEnabled: boolean;
  translationLanguage: string; // ISO 639-1; "" = not chosen yet: use the server default
}

export function defaultProcessing(): ProcessingValues {
  return { ocrEnabled: true, ocrLanguages: "", summaryEnabled: true, translationEnabled: true, translationLanguage: "" };
}

export function processingFromDocument(doc: Document): ProcessingValues {
  return {
    ocrEnabled: doc.ocr_enabled,
    ocrLanguages: doc.ocr_languages,
    summaryEnabled: doc.summary_enabled,
    translationEnabled: doc.translation_enabled,
    translationLanguage: doc.translation_language,
  };
}

export function processingPayload(v: ProcessingValues) {
  return {
    ocr_enabled: v.ocrEnabled,
    ocr_languages: v.ocrLanguages || null,
    summary_enabled: v.summaryEnabled,
    translation_enabled: v.translationEnabled,
    translation_language: v.translationLanguage || null,
  };
}

/** Fit the values to the installed languages (no OCR language: OCR off); the same object when nothing changes. */
export function normalizeProcessing(v: ProcessingValues, ocr: OcrLanguagesResponse): ProcessingValues {
  const codes = ocr.languages.map((l) => l.code);
  const ocrLanguages = keepInstalled(v.ocrLanguages, codes, ocr.default);
  const ocrEnabled = v.ocrEnabled && codes.length > 0;
  const targets = (ocr.translation_languages ?? []).map((l) => l.code);
  const translationLanguage = targets.includes(v.translationLanguage) ? v.translationLanguage : ocr.translation_default;
  return ocrLanguages === v.ocrLanguages && ocrEnabled === v.ocrEnabled && translationLanguage === v.translationLanguage
    ? v
    : { ...v, ocrEnabled, ocrLanguages, translationLanguage };
}
