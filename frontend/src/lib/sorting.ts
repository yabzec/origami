export type DocumentSort = "date_desc" | "date_asc" | "added_desc" | "title_asc";

export const DEFAULT_SORT: DocumentSort = "date_desc";

export const SORT_OPTIONS: readonly { value: DocumentSort; label: string }[] = [
  { value: "date_desc", label: "Document date (newest)" },
  { value: "date_asc", label: "Document date (oldest)" },
  { value: "added_desc", label: "Date added (newest)" },
  { value: "title_asc", label: "Title A–Z" },
];

/** Sort from the URL; unknown values (old bookmarks, typos) fall back to the default. */
export function parseSort(raw: string | null): DocumentSort {
  return SORT_OPTIONS.find((o) => o.value === raw)?.value ?? DEFAULT_SORT;
}

/** Browse page query string: `?folder=` and `?sort=` (omitted when default). */
export function browseSearch(folderId: number | null, sort: DocumentSort): string {
  const params = new URLSearchParams();
  if (folderId !== null) params.set("folder", String(folderId));
  if (sort !== DEFAULT_SORT) params.set("sort", sort);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}
