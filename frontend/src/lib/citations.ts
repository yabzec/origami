export type CitationPart = { kind: "text"; text: string } | { kind: "citation"; n: number };

export function splitCitations(answer: string): CitationPart[] {
  const parts: CitationPart[] = [];
  const pattern = /\[(\d+)\]/g;
  let cursor = 0;
  for (const match of answer.matchAll(pattern)) {
    if (match.index! > cursor) parts.push({ kind: "text", text: answer.slice(cursor, match.index) });
    parts.push({ kind: "citation", n: Number(match[1]) });
    cursor = match.index! + match[0].length;
  }
  if (cursor < answer.length) parts.push({ kind: "text", text: answer.slice(cursor) });
  return parts;
}
