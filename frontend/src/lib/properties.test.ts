import { describe, expect, it } from "vitest";
import { emptyRow, normalizeProperties } from "./properties";

describe("normalizeProperties", () => {
  it("trims and drops empty rows, keeping order", () => {
    expect(
      normalizeProperties([
        { key: " Zeta ", value: " 1 " },
        { key: "", value: "  " },
        { key: "Alfa", value: "" },
      ]),
    ).toEqual({ rows: [{ key: "Zeta", value: "1" }, { key: "Alfa", value: "" }], error: null });
  });

  it("needs a key for every value", () => {
    expect(normalizeProperties([{ key: " ", value: "ACME" }]).error).toBe("Every value needs a key");
  });

  it("rejects duplicate keys ignoring case", () => {
    expect(normalizeProperties([{ key: "Colore", value: "blu" }, { key: "colore ", value: "rosso" }]).error).toBe(
      "Duplicate key: colore",
    );
  });

  it("accepts an empty list", () => {
    expect(normalizeProperties([])).toEqual({ rows: [], error: null });
  });
});

it("emptyRow is a fresh blank row", () => {
  const a = emptyRow();
  expect(a).toEqual({ key: "", value: "" });
  expect(emptyRow()).not.toBe(a);
});
