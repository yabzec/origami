import { describe, expect, it } from "vitest";
import { documentsPollInterval, documentsQueryString } from "./useDocuments";
import type { Document } from "@/lib/types";

const doc = (status: Document["status"]): Document =>
  ({ status }) as Document;

describe("documentsPollInterval", () => {
  it("polls while any document is processing or pending", () => {
    expect(documentsPollInterval([doc("ready"), doc("processing")])).toBe(4000);
    expect(documentsPollInterval([doc("pending")])).toBe(4000);
  });
  it("stops when everything settled", () => {
    expect(documentsPollInterval([doc("ready"), doc("failed")])).toBe(false);
    expect(documentsPollInterval([])).toBe(false);
    expect(documentsPollInterval(undefined)).toBe(false);
  });
});

describe("documentsQueryString", () => {
  const none = { folder: null, tagId: null, docType: null } as const;
  it("includes only active filters", () => {
    expect(documentsQueryString(none)).toBe("");
    expect(documentsQueryString({ ...none, folder: 3, tagId: 2, docType: "pdf" })).toBe(
      "?folder_id=3&tag_id=2&doc_type=pdf",
    );
  });
  it("sends root and dates", () => {
    expect(documentsQueryString({ ...none, folder: "root", dateFrom: "2026-01-01", dateTo: "2026-01-31" })).toBe(
      "?folder_id=root&date_from=2026-01-01&date_to=2026-01-31",
    );
  });
  it("passes a non-default sort", () => {
    expect(documentsQueryString({ ...none, sort: "title_asc" })).toBe("?sort=title_asc");
    expect(documentsQueryString({ ...none, folder: 3, sort: "date_desc" })).toBe("?folder_id=3");
  });
});
