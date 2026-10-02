import type { Document } from "./types";

export type IconKind = "pdf" | "word" | "text" | "image" | "video";

const WORD_EXTENSIONS = [".doc", ".docx", ".odt", ".rtf"];

export function iconKind(doc: Pick<Document, "doc_type" | "original_filename">): IconKind {
  switch (doc.doc_type) {
    case "pdf":
    case "scan":
      return "pdf";
    case "image":
      return "image";
    case "video":
      return "video";
    case "text": {
      const name = (doc.original_filename ?? "").toLowerCase();
      return WORD_EXTENSIONS.some((ext) => name.endsWith(ext)) ? "word" : "text";
    }
  }
}
