import { DEFAULT_SORT, parseSort, type DocumentSort } from "./sorting";

export interface BrowseParams {
  all: boolean; // flat list of every document (today's "All documents")
  folderId: number | null; // null with all=false = root
  sort: DocumentSort;
  tagId: number | null;
  docType: string | null;
  dateFrom: string | null; // YYYY-MM-DD
  dateTo: string | null;
}

export const DEFAULT_BROWSE: BrowseParams = {
  all: false,
  folderId: null,
  sort: DEFAULT_SORT,
  tagId: null,
  docType: null,
  dateFrom: null,
  dateTo: null,
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const int = (raw: string | null) => (raw && /^\d+$/.test(raw) ? Number(raw) : null);
const isoDate = (raw: string | null) => (raw && ISO_DATE.test(raw) ? raw : null);

export function parseBrowseParams(sp: URLSearchParams): BrowseParams {
  return {
    all: sp.get("all") === "1",
    folderId: int(sp.get("folder")),
    sort: parseSort(sp.get("sort")),
    tagId: int(sp.get("tag")),
    docType: sp.get("type") || null,
    dateFrom: isoDate(sp.get("from")),
    dateTo: isoDate(sp.get("to")),
  };
}

export function browseQuery(p: BrowseParams): string {
  const params = new URLSearchParams();
  if (p.all) params.set("all", "1");
  else if (p.folderId !== null) params.set("folder", String(p.folderId));
  if (p.tagId !== null) params.set("tag", String(p.tagId));
  if (p.docType) params.set("type", p.docType);
  if (p.dateFrom) params.set("from", p.dateFrom);
  if (p.dateTo) params.set("to", p.dateTo);
  if (p.sort !== DEFAULT_SORT) params.set("sort", p.sort);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** Identity of what is listed (view + filters, not order): selection resets when it changes. */
export function browseViewKey(p: BrowseParams): string {
  return browseQuery({ ...p, sort: DEFAULT_SORT });
}

export function invalidDateRange(p: BrowseParams): boolean {
  return p.dateFrom !== null && p.dateTo !== null && p.dateFrom > p.dateTo;
}
