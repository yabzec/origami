import { retryLabel } from "./retry";
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

export function translationNote(doc: Pick<Document, "translation_status" | "active_job">, now: Date): string | null {
  if (doc.translation_status === "failed") return "Translation failed — notification sent. Re-process to retry.";
  if (doc.translation_status !== "pending") return null;
  const job = doc.active_job;
  const label = job && job.type === "translate_document" ? retryLabel(job, now) : null;
  return label ? `Translation retrying (${label})` : "Translation pending…";
}
