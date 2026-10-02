import { describe, expect, it } from "vitest";
import { formatDate, todayIso } from "./dates";

describe("dates", () => {
  it("todayIso uses the local calendar date", () => {
    expect(todayIso(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
  });

  it("formatDate renders DD/MM/YYYY and passes through junk", () => {
    expect(formatDate("2019-03-04")).toBe("04/03/2019");
    expect(formatDate("garbage")).toBe("garbage");
  });
});
