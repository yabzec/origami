export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** "2 folders, 1 document"; a kind with zero items is left out (documents shown when both are zero). */
export function itemsLabel(folders: number, documents: number, joiner = ", "): string {
  const parts: string[] = [];
  if (folders > 0) parts.push(plural(folders, "folder"));
  if (documents > 0 || folders === 0) parts.push(plural(documents, "document"));
  return parts.join(joiner);
}
