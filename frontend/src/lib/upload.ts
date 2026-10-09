export interface UploadFields {
  title?: string;
  folderId?: number | null;
  tagIds?: number[];
  ocrLanguages?: string;
  ocrEnabled?: boolean;
  summaryEnabled?: boolean;
  translationEnabled?: boolean;
  documentDate?: string;
}

export function fileStem(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

export function buildUploadForm(file: File, fields: UploadFields): FormData {
  const form = new FormData();
  form.append("file", file);
  if (fields.title) form.append("title", fields.title);
  if (fields.folderId !== null && fields.folderId !== undefined)
    form.append("folder_id", String(fields.folderId));
  if (fields.tagIds && fields.tagIds.length > 0) form.append("tag_ids", fields.tagIds.join(","));
  if (fields.ocrLanguages) form.append("ocr_languages", fields.ocrLanguages);
  if (fields.ocrEnabled === false) form.append("ocr_enabled", "false");
  if (fields.summaryEnabled === false) form.append("summary_enabled", "false");
  if (fields.translationEnabled === false) form.append("translation_enabled", "false");
  if (fields.documentDate) form.append("document_date", fields.documentDate);
  return form;
}
