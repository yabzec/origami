import { expect, it } from "vitest";
import { isNearBottom } from "./scroll";

it("treats positions within the threshold of the bottom as at the bottom", () => {
  expect(isNearBottom(560, 1000, 400)).toBe(true); // 40 px left
  expect(isNearBottom(500, 1000, 400)).toBe(false); // 100 px left: the user scrolled up
  expect(isNearBottom(0, 300, 400)).toBe(true); // content shorter than the view
});
