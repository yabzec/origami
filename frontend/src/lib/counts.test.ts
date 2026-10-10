import { expect, it } from "vitest";
import { itemsLabel, plural } from "./counts";

it("pluralizes", () => {
  expect(plural(1, "folder")).toBe("1 folder");
  expect(plural(3, "document")).toBe("3 documents");
});

it("labels mixed selections and omits an empty kind", () => {
  expect(itemsLabel(2, 1)).toBe("2 folders, 1 document");
  expect(itemsLabel(0, 5)).toBe("5 documents");
  expect(itemsLabel(1, 0)).toBe("1 folder");
  expect(itemsLabel(3, 7, " and ")).toBe("3 folders and 7 documents");
  expect(itemsLabel(0, 0)).toBe("0 documents");
});
