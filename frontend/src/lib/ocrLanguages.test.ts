import { describe, expect, it } from "vitest";
import { DEFAULT_OCR_LANGUAGES, OCR_LANGUAGES } from "./ocrLanguages";

describe("OCR_LANGUAGES", () => {
  it("offers the five supported combinations, default first", () => {
    expect(OCR_LANGUAGES.map((l) => l.value)).toEqual(["ita+eng", "ita", "eng", "deu", "ita+deu"]);
    expect(OCR_LANGUAGES[0].value).toBe(DEFAULT_OCR_LANGUAGES);
  });
});
