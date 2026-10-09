import { describe, expect, it } from "vitest";
import { browseQuery, browseViewKey, DEFAULT_BROWSE, invalidDateRange, parseBrowseParams } from "./browseParams";

const parse = (qs: string) => parseBrowseParams(new URLSearchParams(qs));

describe("browseParams", () => {
  it("parses every key, ignoring junk", () => {
    expect(parse("")).toEqual(DEFAULT_BROWSE);
    expect(parse("folder=3&tag=2&type=pdf&from=2026-01-01&to=2026-02-01&sort=title_asc")).toEqual({
      all: false,
      folderId: 3,
      sort: "title_asc",
      tagId: 2,
      docType: "pdf",
      dateFrom: "2026-01-01",
      dateTo: "2026-02-01",
    });
    expect(parse("all=1").all).toBe(true);
    expect(parse("folder=abc&tag=x&from=yesterday")).toEqual(DEFAULT_BROWSE);
  });

  it("round-trips through the query string, omitting defaults", () => {
    expect(browseQuery(DEFAULT_BROWSE)).toBe("");
    const p = { ...DEFAULT_BROWSE, folderId: 3, dateFrom: "2026-01-01", sort: "title_asc" as const };
    expect(browseQuery(p)).toBe("?folder=3&from=2026-01-01&sort=title_asc");
    expect(parse(browseQuery(p).slice(1))).toEqual(p);
    expect(browseQuery({ ...DEFAULT_BROWSE, all: true, folderId: 5 })).toBe("?all=1"); // all ignores folder
  });

  it("view key changes with view and filters, not with sort", () => {
    const base = browseViewKey(DEFAULT_BROWSE);
    expect(browseViewKey({ ...DEFAULT_BROWSE, sort: "title_asc" })).toBe(base);
    expect(browseViewKey({ ...DEFAULT_BROWSE, folderId: 1 })).not.toBe(base);
    expect(browseViewKey({ ...DEFAULT_BROWSE, dateTo: "2026-01-01" })).not.toBe(base);
  });

  it("flags an inverted range", () => {
    expect(invalidDateRange({ ...DEFAULT_BROWSE, dateFrom: "2026-02-01", dateTo: "2026-01-01" })).toBe(true);
    expect(invalidDateRange({ ...DEFAULT_BROWSE, dateFrom: "2026-01-01", dateTo: "2026-01-01" })).toBe(false);
    expect(invalidDateRange({ ...DEFAULT_BROWSE, dateFrom: "2026-01-01" })).toBe(false);
  });
});
