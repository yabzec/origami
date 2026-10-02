import type { Document } from "./types";

export type TextVariant = "content" | "translation";

export function textVariants(doc: Pick<Document, "translation_status">): TextVariant[] {
  return doc.translation_status === "done" ? ["content", "translation"] : ["content"];
}

const LANGUAGE_LABELS: Record<string, string> = {
  it: "Italiano",
  en: "English",
  de: "Deutsch",
  fr: "Français",
  es: "Español",
};

export function languageLabel(code: string): string {
  return LANGUAGE_LABELS[code] ?? code.toUpperCase();
}
