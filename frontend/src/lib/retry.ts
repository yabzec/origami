import type { ActiveJob, Document } from "./types";

export function relativeTime(target: Date, now: Date): string {
  const seconds = Math.round((target.getTime() - now.getTime()) / 1000);
  if (!Number.isFinite(seconds) || seconds <= 0) return "now";
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${Math.round(seconds / 3600)} h`;
}

export function retryLabel(job: ActiveJob, now: Date): string | null {
  if (job.attempts === 0) return null;
  return `attempt ${job.attempts + 1}/${job.max_attempts}, next ≈ ${relativeTime(new Date(job.run_at), now)}`;
}

/** Last non-empty line: the "ExceptionType: message" line of a stored traceback. */
export function errorHeadline(error: string | null): string {
  const lines = (error ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return lines[lines.length - 1] ?? "";
}

export function processingRetryMessage(
  doc: Pick<Document, "status" | "active_job">,
  now: Date,
): string | null {
  const job = doc.active_job;
  if (doc.status !== "pending" || !job || job.type !== "process_document") return null;
  const label = retryLabel(job, now);
  if (!label) return null;
  const headline = errorHeadline(job.last_error);
  return `Processing failed, retrying (${label})${headline ? `: ${headline}` : ""}`;
}

export function badgeTitle(doc: Pick<Document, "active_job" | "error_message">, now: Date): string | undefined {
  const label = doc.active_job ? retryLabel(doc.active_job, now) : null;
  return label ?? doc.error_message ?? undefined;
}

export function shouldPollDocument(doc: Pick<Document, "status" | "translation_status">): boolean {
  return doc.status === "pending" || doc.status === "processing" || doc.translation_status === "pending";
}
