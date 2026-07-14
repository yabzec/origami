import type { DocType } from "./types";

export function viewerKind(docType: DocType): "pdf" | "image" | "video" | "text" {
  switch (docType) {
    case "scan":
    case "pdf":
      return "pdf";
    case "image":
      return "image";
    case "video":
      return "video";
    case "text":
      return "text";
  }
}
