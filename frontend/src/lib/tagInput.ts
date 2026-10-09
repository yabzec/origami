import type { Tag } from "./types";

export const TAG_COLORS = [
  "#2563eb", "#16a34a", "#dc2626", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#4b5563",
] as const;

const norm = (s: string) => s.trim().toLocaleLowerCase();

export function matchingTags(tags: Tag[], selectedIds: number[], query: string): Tag[] {
  const q = norm(query);
  return tags
    .filter((t) => !selectedIds.includes(t.id) && norm(t.name).includes(q))
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function exactTag(tags: Tag[], query: string): Tag | undefined {
  const q = norm(query);
  return q ? tags.find((t) => norm(t.name) === q) : undefined;
}

export function nextTagColor(tags: Tag[]): string {
  return TAG_COLORS[tags.length % TAG_COLORS.length];
}
