import { describe, expect, it } from "vitest";
import { languageLabel, textVariants } from "./translation";

describe("textVariants", () => {
  it("offers the translation only when it is done", () => {
    expect(textVariants({ translation_status: "done" })).toEqual(["content", "translation"]);
    expect(textVariants({ translation_status: "failed" })).toEqual(["content"]);
    expect(textVariants({ translation_status: null })).toEqual(["content"]);
  });
});

describe("languageLabel", () => {
  it("names known languages and upper-cases unknown codes", () => {
    expect(languageLabel("it")).toBe("Italiano");
    expect(languageLabel("de")).toBe("Deutsch");
    expect(languageLabel("pt")).toBe("PT");
  });
});
