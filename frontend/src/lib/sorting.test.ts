import { describe, expect, it } from "vitest";
import { browseSearch, parseSort, SORT_OPTIONS } from "./sorting";

describe("parseSort", () => {
  it("accepts known values and falls back to document date (newest)", () => {
    expect(parseSort("title_asc")).toBe("title_asc");
    expect(parseSort(null)).toBe("date_desc");
    expect(parseSort("bogus")).toBe("date_desc");
  });
});

describe("browseSearch", () => {
  it("keeps the folder and a non-default sort", () => {
    expect(browseSearch(null, "date_desc")).toBe("");
    expect(browseSearch(3, "date_desc")).toBe("?folder=3");
    expect(browseSearch(null, "added_desc")).toBe("?sort=added_desc");
    expect(browseSearch(3, "title_asc")).toBe("?folder=3&sort=title_asc");
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
