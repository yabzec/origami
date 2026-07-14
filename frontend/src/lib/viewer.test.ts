import { describe, expect, it } from "vitest";
import { viewerKind } from "./viewer";

describe("viewerKind", () => {
  it("maps every doc type", () => {
    expect(viewerKind("scan")).toBe("pdf");
    expect(viewerKind("pdf")).toBe("pdf");
    expect(viewerKind("image")).toBe("image");
    expect(viewerKind("video")).toBe("video");
    expect(viewerKind("text")).toBe("text");
  });
});
