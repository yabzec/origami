import { describe, expect, it } from "vitest";
import { parseSort, SORT_OPTIONS } from "./sorting";

describe("parseSort", () => {
  it("accepts known values and falls back to document date (newest)", () => {
    expect(parseSort("title_asc")).toBe("title_asc");
    expect(parseSort(null)).toBe("date_desc");
    expect(parseSort("bogus")).toBe("date_desc");
  });
});

describe("SORT_OPTIONS", () => {
  it("lists the four orders with their labels", () => {
    expect(SORT_OPTIONS.map((o) => o.label)).toEqual([
      "Document date (newest)",
      "Document date (oldest)",
      "Date added (newest)",
      "Title A–Z",
    ]);
  });
});
