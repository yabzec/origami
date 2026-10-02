import type { Document } from "./types";

export function viewerKind(doc: Pick<Document, "doc_type" | "preview_path">): "pdf" | "image" | "video" | "text" {
  switch (doc.doc_type) {
    case "scan":
    case "pdf":
      return "pdf";
    case "image":
      return "image";
    case "video":
      return "video";
    case "text":
      return doc.preview_path ? "pdf" : "text"; // converted office documents
  }
}
