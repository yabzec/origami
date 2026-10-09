import type { OcrLanguage } from "./types";

/** `ita+eng` → ["ita", "eng"]; Tesseract treats the first one as primary. */
export function splitLanguages(value: string): string[] {
  return value.split("+").filter(Boolean);
}

/** Add at the end or remove; the last remaining language cannot be removed. */
export function toggleLanguage(value: string, code: string): string {
  const current = splitLanguages(value);
  if (!current.includes(code)) return [...current, code].join("+");
  if (current.length === 1) return value;
  return current.filter((c) => c !== code).join("+");
}

export function languagesLabel(value: string, languages: OcrLanguage[]): string {
  const names = new Map(languages.map((l) => [l.code, l.name]));
  const codes = splitLanguages(value);
  return codes.length ? codes.map((c) => names.get(c) ?? c).join(" + ") : "No language";
}
