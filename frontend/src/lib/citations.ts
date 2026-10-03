import type { ChatSource } from "./types";

// Fenced blocks and inline code spans: citations inside them are left alone.
const CODE = /(`{3}[\s\S]*?`{3}|`[^`\n]*`)/;
// [n] not followed by "(" (already a link) or ":" (reference definition).
const CITATION = /\[(\d+)\](?![(:])/g;

/** Rewrite [n] markers as markdown links to the cited document; unknown n stays plain text. */
export function citationsToMarkdown(text: string, sources: ChatSource[]): string {
  if (sources.length === 0) return text;
  const documentByNumber = new Map(sources.map((s) => [s.n, s.document_id]));
  return text
    .split(CODE)
    .map((part, index) =>
      index % 2 === 1
        ? part
        : part.replace(CITATION, (match, n: string) => {
            const documentId = documentByNumber.get(Number(n));
            return documentId ? `[\\[${n}\\]](/documents/${encodeURIComponent(documentId)})` : match;
          }),
    )
    .join("");
}

/** Same-app path (rendered with the router), not a protocol-relative URL. */
export function isInternalHref(href: string): boolean {
  return /^\/(?![/\\])/.test(href);
}

/** Link text produced by citationsToMarkdown, e.g. "[3]". */
export function isCitationLabel(children: unknown): boolean {
  return typeof children === "string" && /^\[\d+\]$/.test(children);
}
