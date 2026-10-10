import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProcessingOptions } from "./ProcessingOptions";
import { defaultProcessing } from "@/lib/processing";

const ocr = vi.hoisted(() => ({ data: undefined as unknown }));
vi.mock("@/hooks/useOcrLanguages", () => ({ useOcrLanguages: () => ocr }));

describe("ProcessingOptions", () => {
  it("does not crash when the server sends no translation languages (older API)", () => {
    ocr.data = { languages: [{ code: "ita", name: "Italian" }], default: "ita" };
    const value = { ...defaultProcessing(), ocrLanguages: "ita", translationEnabled: true, translationLanguage: "it" };
    render(<ProcessingOptions idPrefix="t" value={value} onChange={() => {}} />);
    expect(screen.getByLabelText("Translation")).toBeChecked();
    expect(screen.queryByLabelText("Translate to")).toBeNull();
  });

  it("shows the target select when translation is on", () => {
    ocr.data = {
      languages: [{ code: "ita", name: "Italian" }],
      default: "ita",
      translation_languages: [{ code: "it", name: "Italian" }],
      translation_default: "it",
    };
    const value = { ...defaultProcessing(), ocrLanguages: "ita", translationEnabled: true, translationLanguage: "it" };
    render(<ProcessingOptions idPrefix="t" value={value} onChange={() => {}} />);
    expect(screen.getByLabelText("Translate to")).toHaveValue("it");
  });
});
