import type { PickedFile } from "./batchUpload";
import { defaultProcessing, processingPayload, type ProcessingValues } from "./processing";
import type { DiscPreview, DiscReport, DiscStudy } from "./types";

export const DISC_MAX_FILE_BYTES = 90 * 1024 * 1024; // Cloudflare rejects request bodies over 100 MB
export const DISC_BATCH_BYTES = 50 * 1024 * 1024;
export const DISC_BATCH_FILES = 200;

const SKIP_DIRS = new Set(["viewers", "common"]);
const SKIP_EXTENSIONS = new Set([".exe", ".dll", ".inf", ".ini", ".bak", ".bmp", ".gif", ".tsr", ".p7m"]);
const JPEG_EXTENSIONS = new Set([".jpg", ".jpeg"]);

export interface StagedFile {
  file: File;
  path: string; // path inside the disc, e.g. "DICOM/1/2/a" or "manual/a.jpg"
}

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

/** Path inside the disc: the folder picker puts the picked folder's own name first. */
export function discPath(relativePath: string): string {
  const parts = relativePath.split("/").filter(Boolean);
  return (parts.length > 1 ? parts.slice(1) : parts).join("/");
}

/** Files worth uploading: DICOM, README, reports, the study page and the small JPEG copies. */
export function keepDiscFile(path: string, size: number): boolean {
  if (size > DISC_MAX_FILE_BYTES) return false;
  const parts = path.toLowerCase().split("/");
  const name = parts[parts.length - 1];
  const ext = extension(name);
  if (name === "thumbs.db" || SKIP_EXTENSIONS.has(ext)) return false;
  if (parts.slice(0, -1).some((p) => SKIP_DIRS.has(p))) return false;
  if (parts[0] === "ihe_pdi")
    return (ext === ".htm" && parts.length === 2) || (JPEG_EXTENSIONS.has(ext) && parts.length > 2);
  if (name === "dicomdir" || name === "readme.txt" || ext === ".pdf" || ext === ".dcm") return true;
  if (parts.slice(0, -1).includes("dicom")) return true;
  return ext === ""; // DICOM files often have no extension
}

export function discFiles(picked: PickedFile[]): StagedFile[] {
  return picked
    .map((p) => ({ file: p.file, path: discPath(p.relativePath) }))
    .filter((f) => keepDiscFile(f.path, f.file.size));
}

/** Manually added images; repeated names within the batch get an index prefix so none overwrites another. */
export function manualFiles(files: File[]): StagedFile[] {
  const kept = files.filter((f) => f.size <= DISC_MAX_FILE_BYTES);
  const counts = new Map<string, number>();
  for (const f of kept) counts.set(f.name, (counts.get(f.name) ?? 0) + 1);
  return kept.map((file, i) => ({
    file,
    path: `manual/${(counts.get(file.name) ?? 0) > 1 ? `${i + 1}-` : ""}${file.name}`,
  }));
}

export function uploadBatches(
  files: StagedFile[],
  maxBytes = DISC_BATCH_BYTES,
  maxFiles = DISC_BATCH_FILES,
): StagedFile[][] {
  const batches: StagedFile[][] = [];
  let current: StagedFile[] = [];
  let bytes = 0;
  for (const f of files) {
    if (current.length > 0 && (bytes + f.file.size > maxBytes || current.length >= maxFiles)) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(f);
    bytes += f.file.size;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

export function batchForm(batch: StagedFile[]): FormData {
  const form = new FormData();
  for (const f of batch) {
    form.append("files", f.file);
    form.append("paths", f.path);
  }
  return form;
}

/** Import defaults: X-rays have no text worth OCR and are not translated; the report gets a summary. */
export function discProcessing(): ProcessingValues {
  return { ...defaultProcessing(), ocrEnabled: false, summaryEnabled: true, translationEnabled: false };
}

export interface StudyDraft {
  key: string;
  include: boolean;
  title: string;
  documentDate: string; // "" = server default (today)
  notes: string;
  folderId: number | null;
  tagIds: number[];
  addPatientTag: boolean; // create the suggested patient tag on import
  imageKeys: string[];
  reportKeys: string[];
}

export function reportsFor(study: DiscStudy, reports: DiscReport[]): DiscReport[] {
  return reports.filter((r) => r.study_key === study.key || r.study_key === null);
}

export function draftFromStudy(study: DiscStudy, reports: DiscReport[], folderId: number | null): StudyDraft {
  return {
    key: study.key,
    include: study.images.some((i) => i.readable),
    title: study.title,
    documentDate: study.document_date ?? "",
    notes: "",
    folderId,
    tagIds: study.patient_tag_id !== null ? [study.patient_tag_id] : [],
    addPatientTag: study.patient_tag !== null && study.patient_tag_id === null,
    imageKeys: study.images.filter((i) => i.readable).map((i) => i.key),
    reportKeys: reports.filter((r) => r.study_key === study.key && r.preselected).map((r) => r.key),
  };
}

/** After "Add files": keep the user's edits; only images and reports that are new get selected. */
export function mergeDrafts(
  previous: StudyDraft[],
  before: DiscPreview | null,
  after: DiscPreview,
  folderId: number | null,
): StudyDraft[] {
  const knownReports = new Set((before?.reports ?? []).map((r) => r.key));
  return after.studies.map((study) => {
    const fresh = draftFromStudy(study, after.reports, folderId);
    const old = previous.find((d) => d.key === study.key);
    const oldStudy = before?.studies.find((s) => s.key === study.key);
    if (!old || !oldStudy) return fresh;
    const knownImages = new Set(oldStudy.images.map((i) => i.key));
    return {
      ...old,
      imageKeys: [...old.imageKeys, ...fresh.imageKeys.filter((k) => !knownImages.has(k))],
      reportKeys: [...old.reportKeys, ...fresh.reportKeys.filter((k) => !knownReports.has(k))],
    };
  });
}

export function canImport(drafts: StudyDraft[]): boolean {
  const included = drafts.filter((d) => d.include);
  return included.length > 0 && included.every((d) => d.title.trim() !== "" && d.imageKeys.length > 0);
}

export function commitBody(
  drafts: StudyDraft[],
  preview: DiscPreview,
  patientTagIds: Record<string, number>,
  processing: ProcessingValues,
) {
  return {
    studies: drafts
      .filter((d) => d.include)
      .map((d) => {
        const study = preview.studies.find((s) => s.key === d.key);
        const order = study ? study.images.map((i) => i.key) : d.imageKeys;
        const extra = patientTagIds[d.key];
        return {
          study_key: d.key,
          title: d.title.trim(),
          document_date: d.documentDate || null,
          notes: d.notes,
          folder_id: d.folderId,
          tag_ids: extra !== undefined && !d.tagIds.includes(extra) ? [...d.tagIds, extra] : d.tagIds,
          image_keys: order.filter((k) => d.imageKeys.includes(k)),
          report_keys: d.reportKeys,
        };
      }),
    ...processingPayload(processing),
  };
}
