import { describe, expect, it } from "vitest";
import {
  badgeTitle,
  errorHeadline,
  processingRetryMessage,
  relativeTime,
  retryLabel,
  shouldPollDocument,
} from "./retry";
import type { ActiveJob } from "./types";

const now = new Date("2026-10-03T10:00:00Z");
const job = (over: Partial<ActiveJob> = {}): ActiveJob => ({
  type: "process_document",
  attempts: 1,
  max_attempts: 5,
  run_at: "2026-10-03T10:00:30+00:00",
  last_error: "Traceback (most recent call last):\n  File \"x.py\"\nRuntimeError: provider down\n",
  ...over,
});

describe("relativeTime", () => {
  it("formats seconds, minutes and hours, past as now", () => {
    expect(relativeTime(new Date("2026-10-03T10:00:30Z"), now)).toBe("30 s");
    expect(relativeTime(new Date("2026-10-03T10:02:00Z"), now)).toBe("2 min");
    expect(relativeTime(new Date("2026-10-03T10:30:00Z"), now)).toBe("30 min");
    expect(relativeTime(new Date("2026-10-03T12:00:00Z"), now)).toBe("2 h");
    expect(relativeTime(new Date("2026-10-03T09:59:00Z"), now)).toBe("now");
    expect(relativeTime(new Date("garbage"), now)).toBe("now");
  });
});

describe("retryLabel", () => {
  it("is null before the first failure", () => {
    expect(retryLabel(job({ attempts: 0 }), now)).toBeNull();
  });
  it("names the next attempt and when it runs", () => {
    expect(retryLabel(job(), now)).toBe("attempt 2/5, next ≈ 30 s");
    expect(retryLabel(job({ attempts: 4, run_at: "2026-10-03T10:30:00+00:00" }), now)).toBe(
      "attempt 5/5, next ≈ 30 min",
    );
  });
});

describe("errorHeadline", () => {
  it("returns the exception line of a traceback", () => {
    expect(errorHeadline(job().last_error)).toBe("RuntimeError: provider down");
    expect(errorHeadline("plain message")).toBe("plain message");
    expect(errorHeadline(null)).toBe("");
  });
});

describe("processingRetryMessage", () => {
  it("describes a pending document whose processing is retrying", () => {
    expect(
      processingRetryMessage(
        { status: "pending", active_job: job({ attempts: 2, run_at: "2026-10-03T10:10:00+00:00" }) },
        now,
      ),
    ).toBe("Processing failed, retrying (attempt 3/5, next ≈ 10 min): RuntimeError: provider down");
  });
  it("omits the colon part when there is no error text", () => {
    expect(processingRetryMessage({ status: "pending", active_job: job({ last_error: null }) }, now)).toBe(
      "Processing failed, retrying (attempt 2/5, next ≈ 30 s)",
    );
  });
  it("is null otherwise", () => {
    expect(processingRetryMessage({ status: "processing", active_job: job() }, now)).toBeNull();
    expect(processingRetryMessage({ status: "pending", active_job: job({ attempts: 0 }) }, now)).toBeNull();
    expect(
      processingRetryMessage({ status: "pending", active_job: job({ type: "translate_document" }) }, now),
    ).toBeNull();
    expect(processingRetryMessage({ status: "pending", active_job: null }, now)).toBeNull();
  });
});

describe("badgeTitle", () => {
  it("prefers the retry label, then the error message", () => {
    expect(badgeTitle({ active_job: job(), error_message: "Retrying: x" }, now)).toBe("attempt 2/5, next ≈ 30 s");
    expect(badgeTitle({ active_job: job({ attempts: 0 }), error_message: null }, now)).toBeUndefined();
    expect(badgeTitle({ active_job: null, error_message: "boom" }, now)).toBe("boom");
    expect(badgeTitle({ active_job: null, error_message: null }, now)).toBeUndefined();
  });
});

describe("shouldPollDocument", () => {
  it("polls while processing or while a translation is pending", () => {
    expect(shouldPollDocument({ status: "pending", translation_status: null })).toBe(true);
    expect(shouldPollDocument({ status: "processing", translation_status: null })).toBe(true);
    expect(shouldPollDocument({ status: "ready", translation_status: "pending" })).toBe(true);
    expect(shouldPollDocument({ status: "ready", translation_status: "done" })).toBe(false);
    expect(shouldPollDocument({ status: "failed", translation_status: null })).toBe(false);
  });
});
