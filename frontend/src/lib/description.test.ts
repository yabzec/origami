import { describe, expect, it } from "vitest";
import { isAiDescription, nextDescription } from "./description";

describe("isAiDescription", () => {
  it("is true only while the text equals a non-empty summary", () => {
    expect(isAiDescription("Una bolletta.", "Una bolletta.")).toBe(true);
    expect(isAiDescription("Una bolletta!", "Una bolletta.")).toBe(false);
    expect(isAiDescription("", null)).toBe(false);
    expect(isAiDescription("", "")).toBe(false);
    expect(isAiDescription("  ", "  ")).toBe(false);
  });
});

describe("nextDescription", () => {
  it("follows the server while the field is unedited", () => {
    expect(nextDescription("", "", "Riassunto AI.")).toBe("Riassunto AI.");
  });

  it("keeps the user's edits", () => {
    expect(nextDescription("Mia nota", "", "Riassunto AI.")).toBe("Mia nota");
  });

  it("keeps the field before the first server value is known", () => {
    expect(nextDescription("Bozza", null, "Riassunto AI.")).toBe("Bozza");
  });
});
