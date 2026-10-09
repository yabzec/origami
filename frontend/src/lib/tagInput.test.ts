import { describe, expect, it } from "vitest";
import { exactTag, matchingTags, nextTagColor, TAG_COLORS } from "./tagInput";

const TAGS = [
  { id: 1, name: "Casa", color: "#111111" },
  { id: 2, name: "Auto", color: "#222222" },
  { id: 3, name: "Casalinghi", color: "#333333" },
];

describe("tagInput helpers", () => {
  it("matches by substring, case-insensitive, excluding selected tags", () => {
    expect(matchingTags(TAGS, [1], "cas").map((t) => t.id)).toEqual([3]);
    expect(matchingTags(TAGS, [], "").map((t) => t.id)).toEqual([2, 1, 3]); // sorted by name
  });

  it("finds an exact name ignoring case and spaces", () => {
    expect(exactTag(TAGS, "  casa ")?.id).toBe(1);
    expect(exactTag(TAGS, "cas")).toBeUndefined();
  });

  it("cycles through the palette", () => {
    expect(nextTagColor([])).toBe(TAG_COLORS[0]);
    expect(nextTagColor(TAGS)).toBe(TAG_COLORS[3 % TAG_COLORS.length]);
  });
});
