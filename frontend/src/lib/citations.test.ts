import { describe, expect, it } from "vitest";
import { splitCitations } from "./citations";

describe("splitCitations", () => {
  it("passes plain text through", () => {
    expect(splitCitations("nessuna citazione")).toEqual([{ kind: "text", text: "nessuna citazione" }]);
  });

  it("extracts citation markers", () => {
    expect(splitCitations("La bolletta è di 42 euro [1] pagata a marzo [2].")).toEqual([
      { kind: "text", text: "La bolletta è di 42 euro " },
      { kind: "citation", n: 1 },
      { kind: "text", text: " pagata a marzo " },
      { kind: "citation", n: 2 },
      { kind: "text", text: "." },
    ]);
  });
});
