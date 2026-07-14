export interface SnippetPart {
  text: string;
  highlighted: boolean;
}

export function splitHighlights(text: string): SnippetPart[] {
  const parts: SnippetPart[] = [];
  const pattern = /<b>(.*?)<\/b>/gs;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index! > cursor) parts.push({ text: text.slice(cursor, match.index), highlighted: false });
    if (match[1]) parts.push({ text: match[1], highlighted: true });
    cursor = match.index! + match[0].length;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), highlighted: false });
  return parts;
}
