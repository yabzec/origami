import type { Attachment, Document } from "./types";

export interface ViewTab {
  key: string; // "main" or the attachment id
  label: string;
  attachment: Attachment | null;
}

const ROLE_LABELS: Record<string, string> = { report: "Report" };

export function viewTabs(doc: Pick<Document, "attachments" | "extra_metadata">): ViewTab[] {
  const tabs: ViewTab[] = [{ key: "main", label: doc.extra_metadata ? "Images" : "Document", attachment: null }];
  const seen: Record<string, number> = {};
  for (const attachment of doc.attachments) {
    const total = doc.attachments.filter((a) => a.role === attachment.role).length;
    seen[attachment.role] = (seen[attachment.role] ?? 0) + 1;
    const base = ROLE_LABELS[attachment.role] ?? attachment.role;
    tabs.push({
      key: String(attachment.id),
      label: total > 1 ? `${base} ${seen[attachment.role]}` : base,
      attachment,
    });
  }
  return tabs;
}

export function activeViewTab(tabs: ViewTab[], param: string | null): ViewTab {
  return tabs.find((t) => t.key === param) ?? tabs[0];
}

export function documentLink(documentId: string, attachmentId?: number | null): string {
  return `/documents/${documentId}${attachmentId != null ? `?tab=${attachmentId}` : ""}`;
}

/** Same keys and labels as FIELDS in backend/app/services/study_metadata.py. */
export const STUDY_FIELDS: [string, string][] = [
  ["patient_name", "Patient"],
  ["birth_date", "Birth date"],
  ["sex", "Sex"],
  ["patient_id", "Patient ID"],
  ["study_description", "Study"],
  ["modality", "Modality"],
  ["body_part", "Body part"],
  ["institution", "Institution"],
  ["accession_number", "Accession number"],
  ["device", "Device"],
];

export function studyRows(meta: Record<string, unknown> | null): [string, string][] {
  if (!meta) return [];
  const rows: [string, string][] = STUDY_FIELDS.filter(([key]) => typeof meta[key] === "string" && meta[key]).map(
    ([key, label]) => [label, meta[key] as string],
  );
  const series = Array.isArray(meta.series) ? (meta.series as { description: string; images: number }[]) : [];
  if (series.length > 0) rows.push(["Series", series.map((s) => `${s.description} (${s.images})`).join("; ")]);
  return rows;
}
