import { describe, expect, it } from "vitest";
import { citationsToMarkdown, isCitationLabel, isInternalHref, splitCitations } from "./citations";
import type { ChatSource } from "./types";

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

const SOURCES: ChatSource[] = [
  { n: 1, chunk_id: 7, document_id: "doc-1", title: "Bolletta marzo", page_number: 2 },
  { n: 2, chunk_id: 9, document_id: "doc-2", title: "Contratto", page_number: null },
];

describe("citationsToMarkdown", () => {
  it("turns [n] into links to the cited document, adjacent ones included", () => {
    expect(citationsToMarkdown("Totale 42 euro [1]. Vedi [1][2].", SOURCES)).toBe(
      "Totale 42 euro [\\[1\\]](/documents/doc-1). Vedi [\\[1\\]](/documents/doc-1)[\\[2\\]](/documents/doc-2).",
    );
  });

  it("leaves [n] without a matching source as plain text", () => {
    expect(citationsToMarkdown("Secondo [3] e [0].", SOURCES)).toBe("Secondo [3] e [0].");
  });

  it("does not touch existing markdown links, reference definitions or code", () => {
    const fence = "`".repeat(3);
    const text = `Link [1](https://example.com), nota [2]: testo, codice \`arr[1]\` e\n${fence}\nx[2]\n${fence}`;
    expect(citationsToMarkdown(text, SOURCES)).toBe(text);
  });

  it("returns the text unchanged without sources", () => {
    expect(citationsToMarkdown("Risposta [1].", [])).toBe("Risposta [1].");
  });

  it("recognises app paths and citation labels", () => {
    expect(isInternalHref("/documents/doc-1")).toBe(true);
    expect(isInternalHref("//evil.example/x")).toBe(false);
    expect(isInternalHref("/\\evil.com")).toBe(false);
    expect(isInternalHref("https://example.com")).toBe(false);
    expect(isCitationLabel("[12]")).toBe(true);
    expect(isCitationLabel("Contratto")).toBe(false);
    expect(isCitationLabel(["[1]"])).toBe(false);
  });
});
