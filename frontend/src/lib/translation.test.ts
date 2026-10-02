import { describe, expect, it } from "vitest";
import { languageLabel, textVariants, translationNote } from "./translation";
import type { ActiveJob } from "./types";

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

describe("translationNote", () => {
  const now = new Date("2026-10-03T10:00:00Z");
  const translateJob = (attempts: number): ActiveJob => ({
    type: "translate_document",
    attempts,
    max_attempts: 5,
    run_at: "2026-10-03T10:02:00+00:00",
    last_error: null,
  });

  it("shows pending without retries", () => {
    expect(translationNote({ translation_status: "pending", active_job: translateJob(0) }, now)).toBe(
      "Translation pending…",
    );
    expect(translationNote({ translation_status: "pending", active_job: null }, now)).toBe("Translation pending…");
  });
  it("shows the retry label while retrying", () => {
    expect(translationNote({ translation_status: "pending", active_job: translateJob(1) }, now)).toBe(
      "Translation retrying (attempt 2/5, next ≈ 2 min)",
    );
  });
  it("ignores retries of other job types", () => {
    expect(
      translationNote(
        { translation_status: "pending", active_job: { ...translateJob(2), type: "process_document" } },
        now,
      ),
    ).toBe("Translation pending…");
  });
  it("tells that a notification was sent on failure", () => {
    expect(translationNote({ translation_status: "failed", active_job: null }, now)).toBe(
      "Translation failed — notification sent. Re-process to retry.",
    );
  });
  it("is null when done or not needed", () => {
    expect(translationNote({ translation_status: "done", active_job: null }, now)).toBeNull();
    expect(translationNote({ translation_status: null, active_job: null }, now)).toBeNull();
  });
});
