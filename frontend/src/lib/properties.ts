import type { Property } from "./types";

/** Same rules as the backend PATCH: trim, drop empty rows, a key for every value, unique keys ignoring case. */
export function normalizeProperties(rows: Property[]): { rows: Property[]; error: string | null } {
  const result: Property[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const key = row.key.trim();
    const value = row.value.trim();
    if (!key && !value) continue;
    if (!key) return { rows: result, error: "Every value needs a key" };
    if (seen.has(key.toLowerCase())) return { rows: result, error: `Duplicate key: ${key}` };
    seen.add(key.toLowerCase());
    result.push({ key, value });
  }
  return { rows: result, error: null };
}

export function emptyRow(): Property {
  return { key: "", value: "" };
}
