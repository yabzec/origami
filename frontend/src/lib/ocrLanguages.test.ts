import { describe, expect, it } from "vitest";
import { keepInstalled, languagesLabel, splitLanguages, toggleLanguage } from "./ocrLanguages";

const LANGS = [
  { code: "eng", name: "English" },
  { code: "ita", name: "Italian" },
];

describe("ocrLanguages helpers", () => {
  it("splits a Tesseract language string", () => {
    expect(splitLanguages("ita+eng")).toEqual(["ita", "eng"]);
    expect(splitLanguages("")).toEqual([]);
  });

  it("appends in click order and removes, but never the last one", () => {
    expect(toggleLanguage("ita", "eng")).toBe("ita+eng");
    expect(toggleLanguage("eng", "ita")).toBe("eng+ita");
    expect(toggleLanguage("ita+eng", "ita")).toBe("eng");
    expect(toggleLanguage("eng", "eng")).toBe("eng");
    expect(toggleLanguage("", "deu")).toBe("deu");
  });

  it("labels with names, falling back to codes", () => {
    expect(languagesLabel("ita+eng", LANGS)).toBe("Italian + English");
    expect(languagesLabel("ita+xyz", LANGS)).toBe("Italian + xyz");
    expect(languagesLabel("", LANGS)).toBe("No language");
  });

  it("keeps only installed languages, falling back when none are left", () => {
    expect(keepInstalled("ita+eng", ["eng", "ita"], "eng")).toBe("ita+eng");
    expect(keepInstalled("fra+eng", ["eng", "ita"], "ita")).toBe("eng");
    expect(keepInstalled("fra", ["eng", "ita"], "ita+eng")).toBe("ita+eng");
    expect(keepInstalled("", ["eng"], "eng")).toBe("eng");
  });
});
