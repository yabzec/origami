import type { Attachment, Document } from "./types";

export interface ViewTab {
  key: string; // "main" or the attachment id
  label: string;
  attachment: Attachment | null;
}

const ROLE_LABELS: Record<string, string> = { report: "Report" };

export function viewTabs(doc: Pick<Document, "attachments" | "origin">): ViewTab[] {
  const tabs: ViewTab[] = [
    { key: "main", label: doc.origin === "xray_disc" ? "Images" : "Document", attachment: null },
  ];
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
