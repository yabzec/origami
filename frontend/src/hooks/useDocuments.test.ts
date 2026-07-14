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
  it("includes only active filters", () => {
    expect(documentsQueryString({ folderId: null, tagId: null, docType: null })).toBe("");
    expect(documentsQueryString({ folderId: 3, tagId: 2, docType: "pdf" })).toBe(
      "?folder_id=3&tag_id=2&doc_type=pdf",
    );
  });
});
